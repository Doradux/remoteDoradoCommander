import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_AGENT_TOKEN", "a" * 48)
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "app.sqlite3"))
    with TestClient(app) as c:
        yield c


def start_oauth(client):
    verifier = "b" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
    reg = client.post("/oauth/register", json={"redirect_uris": [redirect], "token_endpoint_auth_method": "none"})
    assert reg.status_code == 201
    cid = reg.json()["client_id"]
    authorize = client.get("/oauth/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": "abc", "code_challenge": challenge, "code_challenge_method": "S256", "resource": "http://127.0.0.1:8765/mcp"})
    assert authorize.status_code == 200
    request_id = re.search(r"name='request_id' value='([^']+)'", authorize.text).group(1)
    approval = client.post("/oauth/approve", data={"request_id": request_id, "password": "secure-test-password-12345"}, follow_redirects=False)
    assert approval.status_code == 303
    redirect_location = urlsplit(approval.headers["location"])
    assert redirect_location.hostname == "chatgpt.com"
    params = parse_qs(redirect_location.query)
    assert params["state"] == ["abc"]
    assert params["iss"] == ["http://127.0.0.1:8765"]
    return cid, verifier, redirect, params["code"][0]


def test_oauth_pkce_replay_refresh_and_mcp(client):
    health = client.get("/health")
    assert health.json() == {"status": "ok", "online_devices": 0}
    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["authorization_servers"] == ["http://127.0.0.1:8765"]
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    cid, verifier, redirect, code = start_oauth(client)
    wrong = client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": redirect, "code_verifier": "q" * 64})
    assert wrong.status_code == 400
    # One-time authorization code is intentionally consumed even for invalid verifier.
    replay = client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": redirect, "code_verifier": verifier})
    assert replay.status_code == 400
    cid, verifier, redirect, code = start_oauth(client)
    grant = client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": redirect, "code_verifier": verifier})
    assert grant.status_code == 200, grant.text
    access, refresh = grant.json()["access_token"], grant.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"}
    initialized = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}, headers=headers)
    assert initialized.json()["result"]["protocolVersion"] == "2025-06-18"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers).status_code == 202
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
    assert any(tool["name"] == "execute_command" for tool in tools.json()["result"]["tools"])
    devices = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_devices", "arguments": {}}}, headers=headers)
    assert '"devices": []' in devices.json()["result"]["content"][0]["text"]
    renewed = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid, "refresh_token": refresh})
    assert renewed.status_code == 200
    assert renewed.json()["access_token"] != access
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid, "refresh_token": refresh}).status_code == 400


def test_bad_registration_and_authentication(client):
    assert client.post("/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]}).status_code == 400
    assert client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/callback"]}).status_code == 400
    cid, verifier, redirect, code = start_oauth(client)
    assert client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": "https://wrong.example", "code_verifier": verifier}).status_code == 400


def test_agent_auth(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer bad", "X-Device-Name": "test"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + "a" * 48, "X-Device-Name": "test"}):
        assert client.get("/health").json()["online_devices"] == 1
    assert client.get("/health").json()["online_devices"] == 0


def test_end_to_end_relay(client):
    from concurrent.futures import ThreadPoolExecutor
    cid, verifier, redirect, code = start_oauth(client)
    grant = client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": redirect, "code_verifier": verifier})
    headers = {"Authorization": "Bearer " + grant.json()["access_token"]}
    with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + "a" * 48, "X-Device-Name": "linux"}) as ws:
        with ThreadPoolExecutor(max_workers=1) as pool:
            rpc = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "read_file", "arguments": {"device": "linux", "path": "/home/demo.txt"}}}
            future = pool.submit(client.post, "/mcp", json=rpc, headers=headers)
            message = ws.receive_json()
            assert message["op"] == "read_file"
            assert message["args"]["path"] == "/home/demo.txt"
            ws.send_json({"id": message["id"], "result": {"content": "Hello from paired device"}})
            outcome = future.result(timeout=10)
            assert outcome.status_code == 200
            assert "Hello from paired device" in outcome.json()["result"]["content"][0]["text"]
