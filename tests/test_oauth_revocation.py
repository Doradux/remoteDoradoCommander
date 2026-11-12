"""Regression for revoking a connected MCP OAuth client."""
import pytest
from fastapi.testclient import TestClient
from remote_dorado.server import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "oauth.sqlite3"))
    with TestClient(app) as c:
        yield c



def test_operator_can_revoke_oauth_client_and_tokens(client):
    import base64
    import hashlib
    import re
    from urllib.parse import parse_qs, urlsplit

    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
    registered = client.post("/oauth/register", json={"redirect_uris": [redirect]})
    assert registered.status_code == 201
    client_id = registered.json()["client_id"]
    auth = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "state": "opaque", "code_challenge": challenge, "code_challenge_method": "S256",
    })
    assert auth.status_code == 200
    request_id = re.search(r"name='request_id' value='([^']+)'", auth.text).group(1)
    approval = client.post("/oauth/approve", data={
        "request_id": request_id, "password": "secure-test-password-12345"
    }, follow_redirects=False)
    code = parse_qs(urlsplit(approval.headers["location"]).query)["code"][0]
    issued = client.post("/oauth/token", data={
        "client_id": client_id, "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect, "code_verifier": verifier,
    })
    assert issued.status_code == 200
    access, refresh = issued.json()["access_token"], issued.json()["refresh_token"]
    auth_header = {"Authorization": "Bearer " + access}
    assert client.post("/mcp", headers=auth_header, json={
        "jsonrpc": "2.0", "id": 9, "method": "tools/list",
    }).status_code == 200
    admin = {"Authorization": "Bearer secure-test-password-12345"}
    assert client.get("/admin/oauth/clients").status_code == 403
    assert [x["client_id"] for x in client.get("/admin/oauth/clients", headers=admin).json()["clients"]] == [client_id]
    assert client.post("/admin/oauth/clients/revoke", json={"client_id": client_id}).status_code == 403
    revoked = client.post("/admin/oauth/clients/revoke", headers=admin, json={"client_id": client_id})
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked_client_id": client_id}
    assert client.post("/mcp", headers=auth_header, json={
        "jsonrpc": "2.0", "id": 10, "method": "tools/list",
    }).status_code == 401
    assert client.post("/oauth/token", data={
        "client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh
    }).status_code == 400
    assert client.get("/admin/oauth/clients", headers=admin).json()["clients"] == []
    assert client.post("/admin/oauth/clients/revoke", headers=admin, json={"client_id": client_id}).status_code == 404
    assert client.post("/admin/oauth/clients/revoke", headers=admin, json={"client_id": "invalid"}).status_code == 400
