"""Cloud client must require a paid account and fail closed on invalid session."""
import json

import httpx
import pytest

from remote_dorado.desktop_cloud import CloudAPI, CloudAccessError, CloudIdentity, validate_cloud_origin


def service(handler):
    transport = httpx.MockTransport(handler)
    return CloudAPI("https://cloud.example.com", transport=transport)


def test_login_only_retains_server_authorized_session():
    requests = []
    def handler(req):
        requests.append(req)
        assert req.url.path == "/desktop/v1/login"
        assert req.method == "POST"
        assert json.loads(req.content)["device_name"] == "office-1"
        return httpx.Response(200, json={
            "account_email": "owner@example.com", "relay_url": "https://owner.relay.example.com",
            "device_name": "office-1", "plan_active": True,
            "agent_token": "A" * 50, "session_token": "S" * 50,
        })
    identity = service(handler).login("OWNER@example.com", "secret example", "office-1")
    assert identity == CloudIdentity(
        "owner@example.com", "https://owner.relay.example.com", "office-1",
        "A" * 50, "S" * 50,
    )
    assert len(requests) == 1


def test_resume_requires_server_check_and_bearer():
    def handler(req):
        assert req.url.path == "/desktop/v1/session"
        assert req.headers["authorization"] == "Bearer " + "S" * 50
        return httpx.Response(200, json={
            "account_email": "owner@example.com",
            "relay_url": "https://owner.relay.example.com",
            "device_name": "office-1", "plan_active": True,
        })
    current = service(handler).resume("S" * 50)
    assert current.agent_token == ""  # The saved device token is held locally in the vault.
    assert current.email == "owner@example.com"


@pytest.mark.parametrize("error,status", [
    ("inactive_subscription", 402), ("invalid_credentials", 401),
    ("too_many_attempts", 429), ("relay_unavailable", 503),
])
def test_login_does_not_connect_on_paid_account_errors(error, status):
    def handler(_req):
        return httpx.Response(status, json={"error": error})
    with pytest.raises(CloudAccessError) as exc:
        service(handler).login("owner@example.com", "password", "desktop")
    assert exc.value.http_status == status


def test_deny_fake_plan_active_or_fake_relay():
    for payload in (
        {"plan_active": False, "account_email": "owner@example.com",
         "relay_url": "https://owner.relay.example.com", "device_name": "pc",
         "agent_token": "A" * 50, "session_token": "S" * 50},
        {"plan_active": True, "account_email": "owner@example.com",
         "relay_url": "http://attacker.example.com", "device_name": "pc",
         "agent_token": "A" * 50, "session_token": "S" * 50},
    ):
        def handler(_req):
            return httpx.Response(200, json=payload)
        with pytest.raises(CloudAccessError):
            service(handler).login("owner@example.com", "secret", "pc")


def test_missing_cloud_provider_url_is_not_hidden_paid_service(monkeypatch):
    monkeypatch.delenv("DORADO_CLOUD_API_URL", raising=False)
    from remote_dorado import desktop_cloud
    monkeypatch.setattr(desktop_cloud, "DEFAULT_CLOUD_API_URL", "")
    with pytest.raises(CloudAccessError, match="no está configurado"):
        CloudAPI()


@pytest.mark.parametrize("origin", [
    "http://cloud.example.com", "https://cloud.example.com/x",
    "https://user:password@cloud.example.com", "file:///tmp/test",
])
def test_unsafe_cloud_api_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        validate_cloud_origin(origin)
