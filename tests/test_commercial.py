"""Regression tests for per-relay device seats and safe revocation."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from remote_dorado.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("DORADO_MAX_DEVICES", "2")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "db.sqlite3"))
    with TestClient(app) as c:
        yield c


def pair(client, name):
    requested = client.post("/pair/device", json={"device_name": name})
    assert requested.status_code == 200, requested.text
    created = requested.json()
    approved = client.post("/pair/approve", data={
        "user_code": created["user_code"], "password": "secure-test-password-12345",
    })
    assert approved.status_code == 200
    token = client.post("/pair/token", json={"device_code": created["device_code"]})
    assert token.status_code == 200, token.text
    return token.json()["agent_token"]


def test_seat_cap_admin_and_revocation(client):
    first = pair(client, "desktop")
    pair(client, "laptop")
    denied = client.post("/pair/device", json={"device_name": "third"})
    assert denied.status_code == 403
    assert denied.json()["error"] == "device_limit_reached"
    renewed = pair(client, "desktop")
    assert first != renewed

    secret = {"Authorization": "Bearer secure-test-password-12345"}
    assert client.get("/admin/devices").status_code == 403
    before = client.get("/admin/devices", headers=secret).json()
    assert before["count"] == 2
    assert before["max_devices"] == 2
    assert {x["name"] for x in before["devices"]} == {"desktop", "laptop"}

    with client.websocket_connect("/agent/ws", headers={
        "Authorization": "Bearer " + renewed, "X-Device-Name": "desktop"
    }) as ws:
        response = client.post("/admin/devices/revoke", headers=secret, json={"device_name": "desktop"})
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with pytest.raises(Exception):
        with client.websocket_connect("/agent/ws", headers={
            "Authorization": "Bearer " + renewed, "X-Device-Name": "desktop"
        }) as ws:
            ws.receive_json()

    assert client.post("/admin/devices/revoke", headers=secret, json={
        "device_name": "../laptop",
    }).status_code == 400
    assert client.post("/admin/devices/revoke", json={"device_name": "laptop"}).status_code == 403
    pair(client, "third")


def test_claim_rechecks_seat_limit(client, monkeypatch):
    monkeypatch.setenv("DORADO_MAX_DEVICES", "1")
    pairs = []
    for name in ("one", "two"):
        requested = client.post("/pair/device", json={"device_name": name}).json()
        client.post("/pair/approve", data={
            "user_code": requested["user_code"], "password": "secure-test-password-12345",
        })
        pairs.append(requested)
    assert client.post("/pair/token", json={"device_code": pairs[0]["device_code"]}).status_code == 200
    assert client.post("/pair/token", json={"device_code": pairs[1]["device_code"]}).status_code == 403



def test_optional_agent_read_only_mode(monkeypatch, tmp_path):
    from remote_dorado.agent import dispatch
    target = tmp_path / "do-not-change.txt"
    target.write_text("original")
    monkeypatch.setenv("DORADO_READ_ONLY", "1")
    assert dispatch("ping", {})["platform"]
    for op, args in [
        ("write_file", {"path": str(target), "content": "changed", "overwrite": True}),
        ("edit_file", {"path": str(target), "old_text": "original", "new_text": "changed"}),
        ("execute_command", {"cwd": str(tmp_path), "command": "true"}),
        ("start_process", {"cwd": str(tmp_path)}),
    ]:
        with pytest.raises(PermissionError, match="read-only"):
            dispatch(op, args)
    assert target.read_text() == "original"




def test_security_guard_against_legacy_tokens_in_commercial_mode(monkeypatch):
    from remote_dorado.server import init_db
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.setenv("DORADO_COMMERCIAL_MODE", "1")
    monkeypatch.setenv("DORADO_AGENT_TOKEN", "x" * 48)
    with pytest.raises(RuntimeError, match="Legacy shared"):
        init_db()


def test_pairing_html_escapes_untrusted_display_values():
    from remote_dorado.ui import pairing_page, authorization_page
    from tests.ui_helpers import page_data
    html = pairing_page("abc' autofocus", "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html
    assert r"\u003cscript\u003e" in html
    assert page_data(html)["deviceName"] == "<script>alert(1)</script>"
    oauth = authorization_page("abc' onclick='bad", "https://client.example")
    assert page_data(oauth)["requestId"] == "abc' onclick='bad"
    assert "<script type='module'" in oauth
    assert "<style>" not in oauth  # no hand-written inline templates survive


def test_release_pipeline_does_not_embed_residential_ip():
    from pathlib import Path
    release_workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml").read_text()
    assert "vars.DORADO_DEFAULT_RELAY_URL" not in release_workflow

