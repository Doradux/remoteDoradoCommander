"""Only the provider with a valid tenant HMAC can provision paid cloud agents."""
from __future__ import annotations

import secrets
import time

import pytest
from fastapi.testclient import TestClient

from remote_dorado.cloud_link import cloud_request_signature
from remote_dorado.entitlements import create_entitlement, derive_tenant_key
from remote_dorado.server import app, database

MASTER = "ab" * 32
CUSTOMER = "alice"


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    key = derive_tenant_key(MASTER, CUSTOMER)
    doc = tmp_path / "entitlement.json"
    import json
    doc.write_text(json.dumps(create_entitlement(CUSTOMER, 2, "active", key)))
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "long-test-admin-password")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "tenant.sqlite3"))
    monkeypatch.setenv("DORADO_COMMERCIAL_MODE", "1")
    monkeypatch.setenv("DORADO_CLOUD_MANAGED", "1")
    monkeypatch.setenv("DORADO_ENTITLEMENT_CUSTOMER", CUSTOMER)
    monkeypatch.setenv("DORADO_ENTITLEMENT_SECRET", key.hex())
    monkeypatch.setenv("DORADO_ENTITLEMENT_FILE", str(doc))
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    with TestClient(app) as client:
        yield client, key, doc


def packet(name="my-computer", customer=CUSTOMER, stamp=None, nonce=None):
    return {
        "customer_id": customer, "email": "alice@example.com",
        "device_name": name,
        "issued_at": int(time.time()) if stamp is None else stamp,
        "nonce": nonce or secrets.token_urlsafe(24),
    }


def send(client, key, data, signature=None):
    return client.post("/internal/cloud/device", json=data, headers={
        "X-Dorado-Cloud-Signature": signature or cloud_request_signature(key, data),
    })


def test_signed_cloud_issuer_provisions_device_without_pairing_password(tenant):
    client, key, doc = tenant
    issued = send(client, key, packet("first"))
    assert issued.status_code == 200, issued.text
    assert issued.json()["device_name"] == "first"
    assert len(issued.json()["agent_token"]) >= 32
    with client.websocket_connect("/agent/ws", headers={
        "Authorization": "Bearer " + issued.json()["agent_token"],
        "X-Device-Name": "first",
    }):
        assert client.get("/health").json()["online_devices"] >= 1


def test_bad_signatures_cross_tenant_expiry_and_replay_rejected(tenant):
    client, key, _ = tenant
    wrong = send(client, key, packet(), signature="a" * 64)
    assert wrong.status_code == 403
    mismatch = send(client, key, packet(customer="bob"))
    assert mismatch.status_code == 403
    old = send(client, key, packet(stamp=int(time.time()) - 300))
    assert old.status_code == 403
    data = packet(nonce="Y" * 32)
    assert send(client, key, data).status_code == 200
    assert send(client, key, data).status_code == 409


def test_paid_seats_enforced_on_tenant(tenant):
    client, key, doc = tenant
    assert send(client, key, packet("first")).status_code == 200
    assert send(client, key, packet("second")).status_code == 200
    assert send(client, key, packet("third")).status_code == 403
    # Rotation for an already registered device is allowed; no extra seat.
    rotated = send(client, key, packet("first"))
    assert rotated.status_code == 200


def test_unpaid_signed_entitlement_blocks_cloud_and_existing_agent(tenant):
    import json
    client, key, doc = tenant
    first = send(client, key, packet("first")).json()
    doc.write_text(json.dumps(create_entitlement(CUSTOMER, 0, "past_due", key)))
    assert send(client, key, packet("new")).status_code == 403
    # Managed cloud policy fails closed even for a previously paired device.
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={
            "Authorization": "Bearer " + first["agent_token"],
            "X-Device-Name": "first",
        }):
            pass


def test_non_managed_relay_never_exposes_provider_issuance(tenant, monkeypatch):
    client, key, _ = tenant
    monkeypatch.delenv("DORADO_CLOUD_MANAGED", raising=False)
    assert send(client, key, packet()).status_code == 404


def test_cloud_logout_revocation_works_even_after_plan_lapses(tenant):
    import json
    client, key, doc = tenant
    token = send(client, key, packet('office')).json()['agent_token']
    doc.write_text(json.dumps(create_entitlement(CUSTOMER, 0, 'past_due', key)))
    signed = packet('office')
    revoked = client.post('/internal/cloud/revoke', json=signed, headers={
        'X-Dorado-Cloud-Signature': cloud_request_signature(key, signed),
    })
    assert revoked.status_code == 200
    assert revoked.json()['revoked'] is True
    assert client.post('/internal/cloud/revoke', json=signed, headers={
        'X-Dorado-Cloud-Signature': cloud_request_signature(key, signed),
    }).status_code == 409
    with database() as db:
        assert db.execute("SELECT count(*) FROM paired_devices WHERE name='office'").fetchone()[0] == 0
