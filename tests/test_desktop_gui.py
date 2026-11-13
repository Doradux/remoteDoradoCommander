"""Native Qt desktop flow smoke tests on offscreen Windows/Linux-compatible Qt."""
from __future__ import annotations

import os
import sys

import pytest

qt = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QCoreApplication, QEvent

from remote_dorado import desktop_gui as gui
from remote_dorado.desktop_state import DesktopPreferences, load_preferences


class InMemoryVault:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, secret):
        assert len(secret) >= 32
        self.data[key] = secret

    def delete(self, key):
        self.data.pop(key, None)


def dispose(window, app):
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(scope="session")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_modes_visible_and_cloud_unconfigured_disables_login(app, monkeypatch):
    monkeypatch.delenv("DORADO_CLOUD_API_URL", raising=False)
    monkeypatch.setattr(gui, "cloud_api_url", lambda: "")
    window = gui.DesktopWindow(
        prefs=DesktopPreferences(), vault=InMemoryVault(), autostart=False,
    )
    try:
        assert window.windowTitle() == "Dorado Relay"
        assert window.selected_mode == "own"
        assert window.own_group.isVisible() is False  # Window has not been shown.
        window.show()
        app.processEvents()
        assert window.own_group.isVisible()
        assert not window.cloud_group.isVisible()
        window._select_mode("cloud")
        app.processEvents()
        assert window.cloud_group.isVisible()
        assert not window.own_group.isVisible()
        assert not window.connect_btn.isEnabled()
        assert "todavía" in window.cloud_note.text()
        assert window.cloud_password.echoMode() == qt.QLineEdit.EchoMode.Password
    finally:
        dispose(window, app)


def test_restore_last_own_connection_without_pairing_again(app, tmp_path, monkeypatch):
    path = tmp_path / "desktop.json"
    monkeypatch.setenv("DORADO_DESKTOP_CONFIG", str(path))
    started = []
    monkeypatch.setattr(gui.AgentThread, "start", lambda self: started.append(
        (self.relay, self.name, self.permission)
    ))
    vault = InMemoryVault()
    relay = "http://127.0.0.1:8765"
    name = "my-computer"
    from remote_dorado.desktop_state import secret_name
    vault.set(secret_name("own-device", relay, name), "A" * 50)

    prefs = DesktopPreferences(
        last_mode="own", own_relay=relay,
        device_name=name, permission="inspect",
    )
    window = gui.DesktopWindow(prefs=prefs, vault=vault, autostart=False)
    try:
        window.resume_previous_connection()
        app.processEvents()
        assert window.pages.currentIndex() == 1
        assert started == [(relay, name, "inspect")]
        assert load_preferences(path).last_mode == "own"
        assert "A" * 50 not in path.read_text()
    finally:
        dispose(window, app)


def test_cloud_remembers_previous_account_but_not_password(app, tmp_path, monkeypatch):
    path = tmp_path / "desktop.json"
    monkeypatch.setenv("DORADO_DESKTOP_CONFIG", str(path))
    monkeypatch.setattr(gui, "cloud_api_url", lambda: "https://cloud.example.com")
    monkeypatch.setattr(gui.AgentThread, "start", lambda self: None)
    vault = InMemoryVault()
    window = gui.DesktopWindow(
        prefs=DesktopPreferences(), vault=vault, autostart=False,
    )
    try:
        window._select_mode("cloud")
        window.device_name.setText("office")
        window.cloud_email.setText("owner@example.com")
        window.cloud_password.setText("dummy_password_DO_NOT_SAVE")
        from remote_dorado.desktop_cloud import CloudIdentity
        identity = CloudIdentity(
            "owner@example.com", "https://owner.relay.example.com",
            "office", "A" * 50, "S" * 50,
        )
        window._cloud_login_success(identity, "files")
        assert window.pages.currentIndex() == 1
        pref = load_preferences(path)
        assert pref.last_mode == "cloud"
        assert pref.cloud_email == "owner@example.com"
        assert pref.cloud_relay == "https://owner.relay.example.com"
        assert "dummy_password_DO_NOT_SAVE" not in path.read_text()
        assert "A" * 50 not in path.read_text()
        assert "S" * 50 not in path.read_text()
        assert len(vault.data) == 2
    finally:
        dispose(window, app)


def test_switching_between_own_and_cloud_restores_each_device(app):
    prefs = DesktopPreferences(last_mode='cloud', device_name='cloud-pc',
                               cloud_device_name='cloud-pc', own_device_name='home-pc')
    window = gui.DesktopWindow(prefs=prefs, vault=InMemoryVault(), autostart=False)
    try:
        assert window.device_name.text() == 'cloud-pc'
        window._select_mode('own')
        assert window.device_name.text() == 'home-pc'
        window._select_mode('cloud')
        assert window.device_name.text() == 'cloud-pc'
    finally:
        dispose(window, app)
