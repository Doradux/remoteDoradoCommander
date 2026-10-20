"""Browser-approved device pairing, including one-time credentials and reconnect."""
import argparse
import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database
from remote_dorado.agent import resolve_startup_config, read_agent_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "pairing.sqlite3"))
    with TestClient(app) as c:
        yield c


def new_pair(client, name="my-computer"):
    response = client.post("/pair/device", json={"device_name": name})
    assert response.status_code == 200
    return response.json()


def approve(client, user_code, password="secure-test-password-12345"):
    return client.post("/pair/approve", data={"user_code": user_code, "password": password})


def claim(client, pair):
    return client.post("/pair/token", json={"device_code": pair["device_code"]})


def test_pairing_requires_browser_approval_then_allows_websocket(client):
    pair = new_pair(client)
    assert pair["user_code"].count("-") == 2
    assert pair["verification_uri_complete"].startswith("http://127.0.0.1:8765/pair?")
    assert claim(client, pair).json()["error"] == "authorization_pending"
    assert client.get("/pair", params={"code": pair["user_code"]}).status_code == 200
    assert approve(client, pair["user_code"], "incorrect").status_code == 403
    assert claim(client, pair).json()["error"] == "authorization_pending"
    assert approve(client, pair["user_code"]).status_code == 200
    granted = claim(client, pair)
    assert granted.status_code == 200
    assert granted.json()["device_name"] == "my-computer"
    secret = granted.json()["agent_token"]
    assert len(secret) >= 32
    assert claim(client, pair).status_code == 400
    with database() as conn:
        row = conn.execute("SELECT token_hash FROM paired_devices WHERE name=?", ("my-computer",)).fetchone()
    assert row and row["token_hash"] != secret  # never persist the raw credential on relay
    with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + secret, "X-Device-Name": "my-computer"}):
        assert client.get("/health").json()["online_devices"] == 1


def test_pair_token_cannot_impersonate_another_device_or_shared_legacy(client):
    pair = new_pair(client, "marcos")
    assert approve(client, pair["user_code"]).status_code == 200
    secret = claim(client, pair).json()["agent_token"]
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + secret, "X-Device-Name": "marcos-hpr"}) as ws:
            ws.receive_json()
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + "x" * 48, "X-Device-Name": "marcos"}) as ws:
            ws.receive_json()


def test_repairing_rotates_previous_token(client):
    first = new_pair(client, "marcos")
    approve(client, first["user_code"])
    old = claim(client, first).json()["agent_token"]
    with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + old, "X-Device-Name": "marcos"}) as existing_ws:
        next_ = new_pair(client, "marcos")
        approve(client, next_["user_code"])
        new = claim(client, next_).json()["agent_token"]
        # A connection authenticated with the rotated token must be closed immediately.
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            existing_ws.receive_json()
    assert old != new
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + old, "X-Device-Name": "marcos"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/agent/ws", headers={"Authorization": "Bearer " + new, "X-Device-Name": "marcos"}):
        assert client.get("/health").json()["online_devices"] == 1


def test_failed_password_approval_is_limited(client):
    pair = new_pair(client)
    for _ in range(5):
        assert approve(client, pair["user_code"], "wrong").status_code == 403
    assert approve(client, pair["user_code"]).status_code == 403
    assert claim(client, pair).json()["error"] == "authorization_pending"


def test_invalid_names_and_credentials_never_allowed(client):
    assert client.post("/pair/device", json={"device_name": "../etc"}).status_code == 400
    assert client.post("/pair/token", json={"device_code": "wrong"}).status_code == 400
    assert approve(client, "UNKNOWN-CODE").status_code == 400


def test_first_launch_browser_pairs_then_returns_without_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    monkeypatch.setenv("DORADO_PUBLIC_URL", "https://relay.example.org")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("DORADO_DEVICE_NAME", raising=False)
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "pc-principal")
    monkeypatch.setattr("remote_dorado.agent.pair_with_relay", lambda url, name: "q" * 48)
    parser = argparse.ArgumentParser()
    url, token, name, config = resolve_startup_config(parser, None)
    assert (url, token, name) == ("https://relay.example.org", "q" * 48, "pc-principal")
    assert read_agent_config()["agent_token"] == "q" * 48
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("Asked again"))
    assert resolve_startup_config(parser, None)[:3] == (url, token, name)
