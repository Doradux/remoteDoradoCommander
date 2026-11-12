"""Reject oversized and ambiguous public auth requests before allocating resources."""
import pytest
from fastapi.testclient import TestClient

from remote_dorado.server import app, database
from remote_dorado.portal import create_owner


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-test-password-12345")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "dorado.sqlite3"))
    monkeypatch.delenv("DORADO_COMMERCIAL_MODE", raising=False)
    monkeypatch.delenv("DORADO_ENTITLEMENT_FILE", raising=False)
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    with TestClient(app) as api:
        yield api


@pytest.mark.parametrize("route", [
    "/pair/device",
    "/pair/token",
    "/admin/devices/revoke",
    "/admin/oauth/clients/revoke",
])
def test_json_body_size_cap(route, client):
    headers = {"Content-Type": "application/json"}
    if route.startswith("/admin/"):
        headers["Authorization"] = "Bearer secure-test-password-12345"
    response = client.post(route, content=b"X" * 20_000, headers=headers)
    assert response.status_code == 413


@pytest.mark.parametrize("route", [
    "/pair/approve",
    "/oauth/approve",
    "/oauth/token",
    "/portal/login",
    "/portal/activate",
])
def test_form_body_size_cap(route, client):
    if route == "/portal/login":
        with database() as db:
            create_owner(db, "owner@example.com", "secure-login-test-password")
    response = client.post(
        route, content=b"password=" + b"x" * 20_000,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413


def test_duplicate_form_fields_rejected(client):
    with database() as db:
        create_owner(db, "owner@example.com", "secure-login-test-password")
    for path, data in (
        ("/pair/approve", "user_code=AAAAAAAAAAAA&password=one&password=two"),
        ("/oauth/token", "client_id=one&client_id=two&grant_type=authorization_code"),
        ("/portal/login", "email=one@example.com&email=two@example.com&password=secret"),
    ):
        response = client.post(path, content=data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
        })
        assert response.status_code == 400


def test_forms_with_wrong_content_type_rejected(client):
    response = client.post("/oauth/token", json={
        "client_id": "foo", "grant_type": "refresh_token",
    })
    assert response.status_code == 400


def test_normal_pairing_still_supported(client):
    pair = client.post("/pair/device", json={"device_name": "test-device"})
    assert pair.status_code == 200
    response = client.post("/pair/approve", data={
        "user_code": pair.json()["user_code"],
        "password": "secure-test-password-12345",
    })
    assert response.status_code == 200
