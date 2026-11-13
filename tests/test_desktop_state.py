"""Remember last selected connection WITHOUT retaining credentials in settings JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from remote_dorado.desktop_state import (
    DesktopPreferences, NativeSecretStore, CredentialUnavailable,
    load_preferences, save_preferences, secret_name,
)


def test_preferences_remember_last_mode_identity_and_auto_reconnect(tmp_path):
    path = tmp_path / "config/desktop.json"
    pref = DesktopPreferences(
        last_mode="cloud", own_relay="https://home.duckdns.org",
        device_name="office-1", permission="files",
        cloud_email="owner@example.com",
        cloud_relay="https://owner.relay.example.org",
        auto_connect=True,
    )
    save_preferences(pref, path)
    loaded = load_preferences(path)
    assert loaded == pref
    text = path.read_text()
    assert "owner@example.com" in text
    for secret in ("password", "agent_token", "session_token", "secret"):
        assert secret not in text
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0


def test_previous_self_hosted_url_survives_mode_switch(tmp_path):
    path = tmp_path / "desktop.json"
    pref = DesktopPreferences(
        last_mode="own", device_name="home",
        own_relay="https://personal.duckdns.org",
    )
    save_preferences(pref, path)
    pref.last_mode = "cloud"
    pref.cloud_email = "owner@example.com"
    save_preferences(pref, path)
    pref2 = load_preferences(path)
    assert pref2.own_relay == "https://personal.duckdns.org"
    assert pref2.last_mode == "cloud"
    pref2.last_mode = "own"
    save_preferences(pref2, path)
    assert load_preferences(path).own_relay == "https://personal.duckdns.org"


def test_passwords_or_tokens_in_preferences_fail_closed(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({
        "last_mode": "cloud", "cloud_email": "owner@example.com",
        "session_token": "UNSAFE",
    }))
    with pytest.raises(ValueError, match="Credentials may not"):
        load_preferences(path)


@pytest.mark.parametrize("values", [
    {"last_mode": "root"},
    {"own_relay": "http://malicious.example"},
    {"device_name": "../my-pc"},
    {"permission": "root"},
    {"cloud_relay": "https://evil.example/path"},
    {"cloud_email": "invalid"},
    {"auto_connect": "yes"},
])
def test_invalid_config_not_saved(values, tmp_path):
    pref = DesktopPreferences(**values)
    with pytest.raises(ValueError):
        save_preferences(pref, tmp_path / "desktop.json")


def test_symlink_cannot_redirect_preferences_to_other_file(tmp_path):
    target = tmp_path / "important"
    target.write_text("untouched")
    path = tmp_path / "desktop.json"
    path.symlink_to(target)
    with pytest.raises(ValueError):
        save_preferences(DesktopPreferences(), path)
    assert target.read_text() == "untouched"


def test_vault_names_are_scoped_by_server_account_and_pc():
    home = secret_name("own-device", "https://home.example.com", "PC")
    cloud = secret_name("cloud-device", "https://cloud.example.com", "PC", "owner@example.com")
    assert home != cloud
    assert cloud != secret_name("cloud-device", "https://cloud.example.com", "PC", "other@example.com")
    assert cloud != secret_name("cloud-session", "https://cloud.example.com", "PC", "owner@example.com")
    assert "owner@example.com" not in cloud
    with pytest.raises(ValueError):
        secret_name("password", "https://home.example.com", "PC")


def test_native_vault_has_no_plaintext_fallback(monkeypatch):
    from remote_dorado import desktop_state as state
    import sys
    class FakeKeyring:
        priority = 0
    class FakeModule:
        @staticmethod
        def get_keyring():
            return FakeKeyring()
    # A missing or unavailable native secret store never produces a credential file.
    from unittest.mock import patch
    class Missing(Exception): pass
    with patch.dict(sys.modules, {"keyring": None}):
        with pytest.raises(CredentialUnavailable):
            NativeSecretStore().get("no-backend")


def test_each_mode_remembers_its_own_device_name(tmp_path):
    path = tmp_path / 'desktop.json'
    prefs = DesktopPreferences(last_mode='cloud', device_name='cloud-pc',
                               cloud_device_name='cloud-pc', own_device_name='home-pc')
    save_preferences(prefs, path)
    restored = load_preferences(path)
    assert restored.own_device_name == 'home-pc'
    assert restored.cloud_device_name == 'cloud-pc'
    assert restored.last_mode == 'cloud'
