"""Cloud desktop auth is always verified server-side, not a local plan flag."""
from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from remote_dorado import control_plane as cp
from remote_dorado import cloud_desktop_auth as ca


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("DORADO_STRIPE_WEBHOOK_SECRET", "whsec_" + "W" * 32)
    monkeypatch.setenv("DORADO_STRIPE_SECRET_KEY", "sk_test_" + "K" * 32)
    monkeypatch.setenv("DORADO_CONTROL_ADMIN_TOKEN", "A" * 48)
    monkeypatch.setenv("DORADO_STRIPE_BASE_PRICE_ID", "price_" + "B" * 24)
    monkeypatch.setenv("DORADO_STRIPE_EXTRA_PRICE_ID", "price_" + "X" * 24)
    monkeypatch.setenv("DORADO_STRIPE_LIVEMODE", "0")
    monkeypatch.setenv("DORADO_ENTITLEMENT_MASTER_KEY", "ab" * 32)
    ca.LOGIN_ATTEMPTS.clear()
    with TestClient(cp.app) as client:
        cp.register_customer(
            "alice", "alice@example.com", "alice.relay.example.com",
            "cus_" + "A" * 15, "sub_" + "S" * 15,
        )
        ca.register_cloud_account("alice", "alice@example.com", "correct horse password")
        yield client


def activate():
    with cp.database() as db:
        db.execute(
            "UPDATE billing SET status='active',desired_seats=2,verified_at=? WHERE customer_id='alice'",
            (int(time.time()),),
        )


def test_inactive_account_cannot_receive_cloud_device(cloud, monkeypatch):
    called = []
    async def provision(*args):
        called.append(args)
        return "T" * 64
    monkeypatch.setattr(ca, "provision_tenant_device", provision)
    login = cloud.post("/desktop/v1/login", json={
        "email": "alice@example.com", "password": "correct horse password",
        "device_name": "home",
    })
    assert login.status_code == 402
    assert login.json()["error"] == "inactive_subscription"
    assert called == []
    assert cloud.get("/desktop/v1/session", headers={
        "Authorization": "Bearer " + "A" * 64,
    }).status_code == 401


def test_active_account_login_persists_session_and_suspends_after_lapse(cloud, monkeypatch):
    activate()
    issued = []
    async def provision(domain, customer_id, email, device):
        issued.append((domain, customer_id, email, device))
        return "T" * 64
    monkeypatch.setattr(ca, "provision_tenant_device", provision)
    wrong = cloud.post("/desktop/v1/login", json={
        "email": "alice@example.com", "password": "wrong",
        "device_name": "home",
    })
    assert wrong.status_code == 401
    login = cloud.post("/desktop/v1/login", json={
        "email": "Alice@Example.com", "password": "correct horse password",
        "device_name": "my-desktop",
    })
    assert login.status_code == 200, login.text
    result = login.json()
    assert result["plan_active"] is True
    assert result["relay_url"] == "https://alice.relay.example.com"
    assert result["account_email"] == "alice@example.com"
    assert len(result["session_token"]) >= 32
    assert len(result["agent_token"]) >= 32
    assert issued == [("alice.relay.example.com", "alice", "alice@example.com", "my-desktop")]
    with cp.database() as db:
        raw = str(db.execute("SELECT session_hash FROM desktop_sessions").fetchone()[0])
        assert raw == hashlib.sha256(result["session_token"].encode()).hexdigest()
        assert result["session_token"] not in raw

    headers = {"Authorization": "Bearer " + result["session_token"]}
    assert cloud.get("/desktop/v1/session", headers=headers).json()["plan_active"] is True
    with cp.database() as db:
        db.execute(
            "UPDATE billing SET status='past_due',desired_seats=0 WHERE customer_id='alice'",
        )
    suspended = cloud.get("/desktop/v1/session", headers=headers)
    assert suspended.status_code == 402
    assert suspended.json()["error"] == "inactive_subscription"
    assert cloud.post("/desktop/v1/login", json={
        "email": "alice@example.com", "password": "correct horse password",
        "device_name": "other",
    }).status_code == 402
    signed_out = cloud.post("/desktop/v1/logout", headers=headers)
    assert signed_out.status_code == 200
    assert cloud.get("/desktop/v1/session", headers=headers).status_code == 401


def test_stale_stripe_reconciliation_blocks_active_login(cloud, monkeypatch):
    activate()
    with cp.database() as db:
        db.execute(
            "UPDATE billing SET verified_at=? WHERE customer_id='alice'",
            (int(time.time()) - 2 * 86400,),
        )
    async def never(*args):
        pytest.fail("Stale billing must not create a tenant device")
    monkeypatch.setattr(ca, "provision_tenant_device", never)
    response = cloud.post("/desktop/v1/login", json={
        "email": "alice@example.com", "password": "correct horse password",
        "device_name": "test-pc",
    })
    assert response.status_code == 402


def test_bad_password_rate_limits_on_cloud_gateway(cloud):
    activate()
    payload = {
        "email": "alice@example.com", "password": "bad", "device_name": "test",
    }
    for i in range(5):
        assert cloud.post("/desktop/v1/login", json=payload).status_code == 401
    assert cloud.post("/desktop/v1/login", json=payload).status_code == 429
    # A valid password cannot immediately bypass the temporary rate limit.
    payload["password"] = "correct horse password"
    assert cloud.post("/desktop/v1/login", json=payload).status_code == 429


def test_reset_account_revokes_saved_desktop_sessions(cloud, monkeypatch):
    activate()
    async def provision(*args):
        return "T" * 64
    monkeypatch.setattr(ca, "provision_tenant_device", provision)
    login = cloud.post("/desktop/v1/login", json={
        "email": "alice@example.com", "password": "correct horse password",
        "device_name": "my-desktop",
    }).json()
    ca.register_cloud_account("alice", "alice@example.com", "different password 1234")
    assert cloud.get("/desktop/v1/session", headers={
        "Authorization": "Bearer " + login["session_token"],
    }).status_code == 401


def test_reject_bogus_or_oversized_credentials(cloud):
    assert cloud.post("/desktop/v1/login", json={"password": "password"}).status_code == 400
    response = cloud.post("/desktop/v1/login",
        content=b"X" * 10_000, headers={"Content-Type": "application/json"})
    assert response.status_code in (400, 413)


def test_logout_revokes_tenant_device_and_server_session(cloud, monkeypatch):
    activate()
    async def provision(*args): return 'T' * 64
    async def revoke(*args): return True
    monkeypatch.setattr(ca, 'provision_tenant_device', provision)
    monkeypatch.setattr(ca, 'revoke_tenant_device', revoke)
    login = cloud.post('/desktop/v1/login', json={
        'email': 'alice@example.com', 'password': 'correct horse password',
        'device_name': 'home',
    }).json()
    header = {'Authorization': 'Bearer ' + login['session_token']}
    answer = cloud.post('/desktop/v1/logout', headers=header)
    assert answer.status_code == 200
    assert answer.json()['device_revoked'] is True
    assert cloud.get('/desktop/v1/session', headers=header).status_code == 401
