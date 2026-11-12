"""Invite-only activation of a new independent customer account."""
import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database
from remote_dorado.portal import issue_invite, owner_exists, invitation_pending


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "local-admin-secret-no-sharing")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_COMMERCIAL_MODE", "1")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "invites.sqlite3"))
    with TestClient(app) as c:
        yield c


def test_invitation_stored_as_hash_and_single_use(client):
    assert client.get("/portal/activate").status_code == 503
    with database() as conn:
        invite = issue_invite(conn, "pilot@Example.org")
    assert invitation_pending()
    with database() as conn:
        stored = conn.execute("SELECT email,code_hash,expires FROM portal_invitation WHERE id=1").fetchone()
    assert stored["email"] == "pilot@example.org"
    assert stored["code_hash"] != invite
    assert invite not in stored["code_hash"]

    start = client.get("/portal/login", follow_redirects=False)
    assert start.status_code == 303
    assert start.headers["location"] == "/portal/activate"
    html = client.get("/portal/activate")
    assert html.status_code == 200
    assert "Activa tu espacio" in html.text
    assert invite not in html.text

    assert client.post("/portal/activate", data={
        "email": "pilot@example.org", "code": "incorrect-code-00000000000000000000000000000",
        "password": "pilot-passphrase-1234", "repeat": "pilot-passphrase-1234",
    }).status_code == 200
    assert not owner_exists()
    good = client.post("/portal/activate", data={
        "email": "Pilot@example.org", "code": invite,
        "password": "pilot-passphrase-1234", "repeat": "pilot-passphrase-1234",
    }, follow_redirects=False)
    assert good.status_code == 303
    assert good.headers["location"] == "/portal/login"
    assert owner_exists()
    assert not invitation_pending()
    assert client.post("/portal/activate", data={
        "email": "pilot@example.org", "code": invite,
        "password": "pilot-passphrase-1234", "repeat": "pilot-passphrase-1234",
    }).status_code == 403
    with database() as conn, pytest.raises(ValueError):
        issue_invite(conn, "attacker@example.org")
    assert client.post("/portal/login", data={
        "email": "pilot@example.org", "password": "pilot-passphrase-1234"
    }, follow_redirects=False).status_code == 303


def test_invitation_regeneration_revokes_old_code(client):
    with database() as conn:
        old = issue_invite(conn, "owner@one.example.org")
    with database() as conn:
        current = issue_invite(conn, "owner@one.example.org")
    assert old != current
    data = {
        "email": "owner@one.example.org", "password": "secure-password-1234",
        "repeat": "secure-password-1234",
    }
    assert client.post("/portal/activate", data={**data, "code": old}).status_code == 200
    assert not owner_exists()
    assert client.post("/portal/activate", data={**data, "code": current},
                       follow_redirects=False).status_code == 303


def test_invite_expiry_and_login_failures(client):
    with database() as conn:
        code = issue_invite(conn, "first@pilots.example.org")
        conn.execute("UPDATE portal_invitation SET expires=1")
    assert not invitation_pending()
    assert client.get("/portal/activate").status_code == 503
    with database() as conn:
        code = issue_invite(conn, "first@pilots.example.org")
    data = {
        "email": "first@pilots.example.org", "code": "Q" * len(code),
        "password": "long-password-12345", "repeat": "long-password-12345",
    }
    for _ in range(8):
        assert client.post("/portal/activate", data=data).status_code == 200
    assert client.post("/portal/activate", data={**data, "code": code}).status_code == 429
    assert not owner_exists()
