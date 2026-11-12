"""Signed seat manifests: enforce real paid slots without exposing the billing API."""
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from remote_dorado.entitlements import (
    EntitlementError, MAX_TTL, check_entitlement, create_entitlement, derive_tenant_key,
)
from remote_dorado.server import app, database, max_devices
from scripts.provision_pilot import provision


MASTER = "a3" * 32


def test_signatures_cannot_be_forged_or_reused_across_customers():
    first = derive_tenant_key(MASTER, "alice")
    other = derive_tenant_key(MASTER, "bob")
    assert first != other
    envelope = create_entitlement("alice", 4, "active", first)
    assert check_entitlement(envelope, "alice", first) == 4
    with pytest.raises(EntitlementError):
        check_entitlement(envelope, "bob", other)
    with pytest.raises(EntitlementError):
        check_entitlement(envelope, "alice", other)
    envelope["payload"]["seats"] = 99
    with pytest.raises(EntitlementError):
        check_entitlement(envelope, "alice", first)


def test_zero_seats_does_not_mean_unlimited_and_signed_expiry_is_enforced():
    key = derive_tenant_key(MASTER, "alice")
    now = int(time.time())
    expired = create_entitlement("alice", 2, "active", key, issued_at=now-120, ttl=60)
    with pytest.raises(EntitlementError, match="Expired"):
        check_entitlement(expired, "alice", key, now=now)
    withheld = create_entitlement("alice", 0, "past_due", key)
    assert check_entitlement(withheld, "alice", key) == 0
    with pytest.raises(EntitlementError):
        create_entitlement("alice", 4, "unpaid", key)
    with pytest.raises(EntitlementError):
        create_entitlement("alice", 101, "active", key)
    with pytest.raises(EntitlementError):
        create_entitlement("alice", 2, "active", key, ttl=MAX_TTL+1)
    with pytest.raises(EntitlementError):
        derive_tenant_key("weak", "alice")


def test_provisioned_signed_relay_starts_frozen_until_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ENTITLEMENT_MASTER_KEY", MASTER)
    folder = provision(tmp_path / "pilots", "alice", "alice.relay.example.com", 19411,
                       signed_entitlements=True)
    env_content = (folder / ".env").read_text()
    assert MASTER not in env_content
    assert "DORADO_ENTITLEMENT_CUSTOMER=alice" in env_content
    assert "DORADO_ENTITLEMENT_FILE=/app/entitlements/current.json" in env_content
    assert "entitlements:/app/entitlements:ro" in (folder / "compose.yaml").read_text()
    signed = json.loads((folder / "entitlements/current.json").read_text())
    assert signed["payload"]["seats"] == 0
    assert signed["payload"]["status"] == "pending"

    for line in env_content.splitlines():
        if line.startswith("DORADO_") and "=" in line:
            name, value = line.split("=", 1)
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "relay.sqlite3"))
    monkeypatch.setenv("DORADO_ENTITLEMENT_FILE", str(folder / "entitlements/current.json"))
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    key = derive_tenant_key(MASTER, "alice")
    existing_token = "token-for-signed-entitlement-test"

    with TestClient(app) as client:
        assert max_devices() == 0
        refused = client.post("/pair/device", json={"device_name": "first-pc"})
        assert refused.status_code == 403
        assert refused.json()["error"] == "device_limit_reached"

        document = create_entitlement("alice", 2, "active", key)
        (folder / "entitlements/current.json").write_text(json.dumps(document))
        assert max_devices() == 2
        assert client.post("/pair/device", json={"device_name": "first-pc"}).status_code == 200
        with database() as conn:
            conn.execute("INSERT INTO paired_devices(name,token_hash,created) VALUES (?,?,?)",
                         ("existing-pc", hashlib.sha256(existing_token.encode()).hexdigest(), int(time.time())))
            conn.execute("INSERT INTO paired_devices(name,token_hash,created) VALUES (?,?,?)",
                         ("second-pc", "b"*64, int(time.time())))
        assert client.post("/pair/device", json={"device_name": "third-pc"}).status_code == 403

        pending = create_entitlement("alice", 0, "past_due", key)
        (folder / "entitlements/current.json").write_text(json.dumps(pending))
        assert max_devices() == 0
        assert client.post("/pair/device", json={"device_name": "third-pc"}).status_code == 403
        # Existing device credentials still authorize, even with no new paid seats.
        with client.websocket_connect("/agent/ws", headers={
            "Authorization": "Bearer " + existing_token,
            "X-Device-Name": "existing-pc",
        }):
            assert client.get("/health").json()["online_devices"] >= 1


def test_entitlement_missing_invalid_fails_closed_after_server_started(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-local-password-12345")
    monkeypatch.setenv("DORADO_COMMERCIAL_MODE", "1")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "dorado.sqlite3"))
    monkeypatch.setenv("DORADO_ENTITLEMENT_CUSTOMER", "alice")
    monkeypatch.setenv("DORADO_ENTITLEMENT_SECRET", derive_tenant_key(MASTER, "alice").hex())
    monkeypatch.setenv("DORADO_ENTITLEMENT_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    with TestClient(app) as client:
        response = client.post("/pair/device", json={"device_name": "unpaid-pc"})
        assert response.status_code == 503
        assert response.json()["error"] == "billing_entitlement_unavailable"
        admin = client.get("/admin/devices", headers={
            "Authorization": "Bearer secure-local-password-12345",
        })
        assert admin.status_code == 200
        assert admin.json()["billing_available"] is False
