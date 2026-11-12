"""Commercial single-owner portal and approval-flow integration tests."""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database
from remote_dorado.portal import create_owner, safe_next, password_hash, password_matches


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "test-admin-password-not-for-customers")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_MAX_DEVICES", "2")
    monkeypatch.setenv("DORADO_COMMERCIAL_MODE", "1")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "pilot.sqlite3"))
    with TestClient(app) as api:
        with database() as conn:
            create_owner(conn, "Owner@Example.com", "long-unique-test-password-12")
        yield api


def login(client, destination="/portal"):
    response = client.post("/portal/login", data={
        "email": "owner@example.com", "password": "long-unique-test-password-12",
        "next": destination,
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response


def csrf_from(html):
    value = re.search(r"name='csrf' value='([^']+)'", html)
    assert value is not None
    return value.group(1)


def test_password_storage_and_duplicate_owner(client):
    with database() as conn:
        row = conn.execute("SELECT email,password_hash FROM portal_owner WHERE id=1").fetchone()
    assert row["email"] == "owner@example.com"
    assert "long-unique-test-password-12" not in row["password_hash"]
    assert password_matches("long-unique-test-password-12", row["password_hash"])
    assert not password_matches("not-the-password", row["password_hash"])
    with database() as conn, pytest.raises(ValueError, match="already provisioned"):
        create_owner(conn, "another@example.com", "another-unique-long-password-5678")
    with pytest.raises(ValueError):
        password_hash("too-short")


def test_login_dashboard_session_cookie_csrf_logout(client):
    assert client.get("/portal", follow_redirects=False).status_code == 303
    assert "/portal/login" in client.get("/portal", follow_redirects=False).headers["location"]
    assert client.get("/portal/login").status_code == 200
    incorrect = client.post("/portal/login", data={
        "email": "owner@example.com", "password": "bad-guess",
    })
    assert incorrect.status_code == 200
    assert "No se pudo iniciar" in incorrect.text

    logged = login(client)
    assert "HttpOnly" in logged.headers["set-cookie"]
    assert "samesite=lax" in logged.headers["set-cookie"].lower()
    assert "/portal" == logged.headers["location"]
    dash = client.get("/portal")
    assert dash.status_code == 200
    assert "Mis dispositivos" in dash.text
    assert "owner@example.com" in dash.text
    csrf = csrf_from(dash.text)

    assert client.post("/portal/logout", data={"csrf": "incorrect"}, follow_redirects=False).status_code == 403
    assert client.get("/portal").status_code == 200
    assert client.post("/portal/logout", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    assert client.get("/portal", follow_redirects=False).status_code == 303


def test_limit_login_attempts_and_reject_redirects(client):
    for _ in range(8):
        assert client.post("/portal/login", data={
            "email": "owner@example.com", "password": "incorrect",
        }).status_code == 200
    assert client.post("/portal/login", data={
        "email": "owner@example.com", "password": "long-unique-test-password-12",
    }).status_code == 429

    # A separate account fixture proves the return target is validated.
    assert safe_next("https://example.com") == "/portal"
    assert safe_next("//example.com/") == "/portal"
    assert safe_next("/portal/../admin") == "/portal"
    assert safe_next("/pair?code=ABCD-EFGH") == "/pair?code=ABCD-EFGH"


def test_device_linking_uses_portal_session_and_csrf_not_admin_password(client):
    created = client.post("/pair/device", json={"device_name": "work-pc"})
    assert created.status_code == 200
    data = created.json()
    blocked = client.get("/pair", params={"code": data["user_code"]}, follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/portal/login?")

    # Even knowing the server's master admin secret does not bypass commercial owner login.
    blocked = client.post("/pair/approve", data={
        "user_code": data["user_code"], "password": "test-admin-password-not-for-customers",
    })
    assert blocked.status_code == 403

    login(client, "/pair?code=" + data["user_code"])
    approval_page = client.get("/pair", params={"code": data["user_code"]})
    assert approval_page.status_code == 200
    assert "work-pc" in approval_page.text
    assert "Tu sesión verificada" in approval_page.text
    assert "name='password'" not in approval_page.text
    csrf = csrf_from(approval_page.text)
    assert client.post("/pair/approve", data={"user_code": data["user_code"], "csrf": "wrong"}).status_code == 403
    approved = client.post("/pair/approve", data={"user_code": data["user_code"], "csrf": csrf})
    assert approved.status_code == 200
    claim = client.post("/pair/token", json={"device_code": data["device_code"]})
    assert claim.status_code == 200
    token = claim.json()["agent_token"]

    dash = client.get("/portal")
    assert "work-pc" in dash.text
    csrf = csrf_from(dash.text)
    assert client.post("/portal/devices/revoke", data={
        "csrf": "invalid", "device_name": "work-pc",
    }).status_code == 403
    assert client.post("/portal/devices/revoke", data={
        "csrf": csrf, "device_name": "work-pc",
    }, follow_redirects=False).status_code == 303
    assert "work-pc" not in client.get("/portal").text
    with database() as conn:
        assert not conn.execute("SELECT 1 FROM paired_devices WHERE name='work-pc'").fetchone()


def test_oauth_requires_portal_login_and_csrf_and_can_revoke_client(client):
    verifier = "z" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
    reg = client.post("/oauth/register", json={"redirect_uris": [redirect]})
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "state": "abc123",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    blocked = client.get("/oauth/authorize", params=params, follow_redirects=False)
    assert blocked.status_code == 303
    assert "/portal/login" in blocked.headers["location"]

    login(client)
    auth = client.get("/oauth/authorize", params=params)
    assert auth.status_code == 200
    assert "name='password'" not in auth.text
    csrf = csrf_from(auth.text)
    request_id = re.search(r"name='request_id' value='([^']+)'", auth.text).group(1)
    assert client.post("/oauth/approve", data={
        "request_id": request_id, "csrf": "tampered",
    }).status_code == 403
    granted = client.post("/oauth/approve", data={
        "request_id": request_id, "csrf": csrf,
    }, follow_redirects=False)
    assert granted.status_code == 303
    code = parse_qs(urlsplit(granted.headers["location"]).query)["code"][0]
    issued = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "client_id": client_id,
        "code": code, "redirect_uri": redirect, "code_verifier": verifier,
    })
    assert issued.status_code == 200
    token = issued.json()["access_token"]
    rpc = {"jsonrpc": "2.0", "id": 123, "method": "tools/list"}
    assert client.post("/mcp", headers={"Authorization": "Bearer " + token}, json=rpc).status_code == 200
    assert client.post("/mcp", headers={"Authorization": "Bearer " + token},
                       content=b"x" * 3_000_001).status_code == 413
    # Audit failures without retaining the remote command arguments or file paths.
    attempted = client.post("/mcp", headers={"Authorization": "Bearer " + token}, json={
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "read_file", "arguments": {
            "device": "offline", "path": "/private/do-not-log-this-path",
        }},
    })
    assert attempted.status_code == 200
    assert attempted.json()["result"]["isError"] is True
    with database() as conn:
        events = conn.execute("SELECT tool,device,status FROM action_audit").fetchall()
    assert len(events) == 1
    assert (events[0]["tool"], events[0]["device"], events[0]["status"]) == (
        "read_file", "offline", "error"
    )
    assert "/private/do-not-log-this-path" not in client.get("/portal").text

    dash = client.get("/portal")
    assert "Actividad reciente" in dash.text
    assert "read_file" in dash.text
    assert client_id in dash.text
    assert client.post("/portal/oauth/revoke", data={
        "client_id": client_id, "csrf": "wrong",
    }).status_code == 403
    revoked = client.post("/portal/oauth/revoke", data={
        "client_id": client_id, "csrf": csrf_from(dash.text),
    }, follow_redirects=False)
    assert revoked.status_code == 303
    assert client.post("/mcp", headers={"Authorization": "Bearer " + token}, json=rpc).status_code == 401


def test_password_rotation_revokes_all_browser_sessions(client):
    login(client)
    csrf = csrf_from(client.get("/portal").text)
    assert client.post("/portal/security", data={
        "csrf": csrf, "current": "long-unique-test-password-12",
        "new": "new-password-123456789", "repeat": "different-password",
    }).status_code == 400
    response = client.post("/portal/security", data={
        "csrf": csrf, "current": "long-unique-test-password-12",
        "new": "new-password-123456789", "repeat": "new-password-123456789",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/portal", follow_redirects=False).status_code == 303
    assert client.post("/portal/login", data={
        "email": "owner@example.com", "password": "long-unique-test-password-12",
    }).status_code == 200
    assert client.post("/portal/login", data={
        "email": "owner@example.com", "password": "new-password-123456789",
    }, follow_redirects=False).status_code == 303
