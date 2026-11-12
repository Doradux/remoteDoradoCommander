"""Bound unauthenticated OAuth DCR usage without preventing MCP discovery."""
import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-admin-test-pass-123")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "dcr.sqlite3"))
    with TestClient(app) as c:
        yield c


def test_dcr_limit_and_no_unauthenticated_data_leak(client):
    redirect = {"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]}
    assert client.get("/.well-known/oauth-authorization-server").status_code == 200
    for _ in range(20):
        assert client.post("/oauth/register", json=redirect).status_code == 201
    refused = client.post("/oauth/register", json=redirect)
    assert refused.status_code == 429
    assert refused.json()["error"] == "registration_limit"
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 20
        assert conn.execute("SELECT count(*) FROM oauth_registration_events").fetchone()[0] == 20
    assert client.get("/admin/oauth/clients").status_code == 403
    assert client.post("/oauth/register", content=b"x" * 16385,
                       headers={"Content-Type": "application/json"}).status_code == 413
