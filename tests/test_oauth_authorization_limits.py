"""OAuth authorization request exhaustion cannot grow the SQLite store indefinitely."""
import base64
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-local-authorization-password")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "oauth.sqlite3"))
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("DORADO_COMMERCIAL_MODE", raising=False)
    with TestClient(app) as test:
        yield test


def test_oauth_authorization_is_bounded_per_client_and_recovers_after_expiry(client):
    uri = "https://chatgpt.com/connector_platform_oauth_redirect"
    registration = client.post("/oauth/register", json={"redirect_uris": [uri]})
    assert registration.status_code == 201
    client_id = registration.json()["client_id"]
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest(),
    ).decode().rstrip("=")
    params = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": uri, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "safe",
    }
    for _ in range(20):
        assert client.get("/oauth/authorize", params=params).status_code == 200
    blocked = client.get("/oauth/authorize", params=params)
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "too_many_authorization_requests"
    with database() as conn:
        assert conn.execute(
            "SELECT count(*) FROM authorization_requests WHERE client_id=?",
            (client_id,),
        ).fetchone()[0] == 20
        conn.execute(
            "UPDATE authorization_requests SET expires=? WHERE client_id=?",
            (int(time.time()) - 1, client_id),
        )
    assert client.get("/oauth/authorize", params=params).status_code == 200
    with database() as conn:
        assert conn.execute(
            "SELECT count(*) FROM authorization_requests WHERE client_id=?",
            (client_id,),
        ).fetchone()[0] == 1


def test_expired_tokens_purged_when_issuing_new_ones(client):
    from remote_dorado.server import issue_tokens
    now = int(time.time())
    with database() as conn:
        conn.execute("INSERT INTO clients VALUES (?,?)", ("test-client", "[]"))
        conn.execute(
            "INSERT INTO tokens VALUES (?,?,?,?)",
            ("expired-token", "test-client", "access", now - 100),
        )
        issue_tokens(conn, "test-client")
        assert conn.execute(
            "SELECT count(*) FROM tokens WHERE token_hash='expired-token'",
        ).fetchone()[0] == 0
