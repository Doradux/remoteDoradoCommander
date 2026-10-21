import os
from pathlib import Path

import pytest

from remote_dorado.agent import dispatch, checked_path, websocket_address


def test_agent_read_write_edit_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(tmp_path))
    dest = tmp_path / "folder" / "demo.txt"
    assert dispatch("write_file", {"path": str(dest), "content": "hello"})["bytes"] == 5
    with pytest.raises(FileExistsError):
        dispatch("write_file", {"path": str(dest), "content": "oops"})
    assert dispatch("read_file", {"path": str(dest)})["content"] == "hello"
    assert dispatch("edit_file", {"path": str(dest), "old_text": "hello", "new_text": "world"})["replacements"] == 1
    assert dispatch("read_file", {"path": str(dest)})["content"] == "world"
    assert any("demo.txt" in s for s in dispatch("search_files", {"path": str(tmp_path), "pattern": "*.txt"})["matches"])
    assert dispatch("list_directory", {"path": str(dest.parent)})["entries"][0]["name"] == "demo.txt"
    cmd = dispatch("execute_command", {"cwd": str(tmp_path), "command": "echo dorado", "timeout_seconds": 5})
    assert cmd["returncode"] == 0 and "dorado" in cmd["stdout"]


def test_full_filesystem_wildcard_is_explicit(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'elsewhere' / 'file.txt'
    monkeypatch.setenv('DORADO_ALLOWED_ROOTS', str(home))
    with pytest.raises(PermissionError):
        checked_path(str(outside))
    monkeypatch.setenv('DORADO_ALLOWED_ROOTS', '*')
    assert checked_path(str(outside)) == outside.resolve()
    monkeypatch.setenv('DORADO_ALLOWED_ROOTS', '**')
    with pytest.raises(PermissionError):
        checked_path(str(outside))

def test_root_escape_and_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(tmp_path))
    with pytest.raises(PermissionError):
        checked_path(str(tmp_path / ".." / "outside"))
    assert websocket_address("https://test.example").startswith("wss://")
    assert websocket_address("http://localhost:8765") == "ws://localhost:8765/agent/ws"
