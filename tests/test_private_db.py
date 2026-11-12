"""SQLite relay credential database permissions must be private on Unix."""
from __future__ import annotations

import os

import pytest

from remote_dorado.server import init_db


@pytest.fixture
def relay_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_PUBLIC_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("DORADO_ADMIN_PASSWORD", "secure-private-file-password-123")
    monkeypatch.setenv("DORADO_DB", str(tmp_path / "data" / "relay.sqlite3"))
    monkeypatch.delenv("DORADO_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("DORADO_ENTITLEMENT_FILE", raising=False)
    monkeypatch.delenv("DORADO_COMMERCIAL_MODE", raising=False)
    return tmp_path / "data" / "relay.sqlite3"


@pytest.mark.skipif(os.name != "posix", reason="Unix mode checks")
def test_new_database_created_private(relay_settings):
    init_db()
    assert relay_settings.is_file()
    assert relay_settings.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name != "posix", reason="Unix mode checks")
def test_existing_legacy_db_file_fixed_to_private(relay_settings):
    relay_settings.parent.mkdir()
    relay_settings.write_bytes(b"")
    relay_settings.chmod(0o644)
    init_db()
    assert relay_settings.stat().st_mode & 0o077 == 0


def test_symlinked_database_refused(relay_settings, tmp_path):
    target = tmp_path / "important.sqlite3"
    target.write_bytes(b"do not modify")
    relay_settings.parent.mkdir()
    relay_settings.symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        init_db()
    assert target.read_bytes() == b"do not modify"
