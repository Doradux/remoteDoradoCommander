"""Permission profiles are enforced by the device, never by MCP parameters."""
from __future__ import annotations
import argparse

import pytest

from remote_dorado.agent import (
    ask_permission, check_permission, dispatch, permission_profile,
    PERMISSION_PROFILES, read_agent_config, save_agent_config,
    resolve_startup_config,
)


def test_profiles_gate_every_mutating_operation(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    file = root / "test.txt"
    file.write_text("original")
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(root))

    for profile in ("inspect", "files", "full"):
        monkeypatch.setenv("DORADO_AGENT_PERMISSION", profile)
        monkeypatch.delenv("DORADO_READ_ONLY", raising=False)
        assert permission_profile() == profile
        assert dispatch("read_file", {"path": str(file)})["content"] == "original"
        if profile == "inspect":
            with pytest.raises(PermissionError, match="read-only"):
                dispatch("edit_file", {"path": str(file), "old_text": "original",
                                       "new_text": "changed"})
            with pytest.raises(PermissionError, match="read-only"):
                dispatch("execute_command", {"cwd": str(root), "command": "echo ok"})
        elif profile == "files":
            assert dispatch("edit_file", {"path": str(file), "old_text": "original",
                                         "new_text": "changed"})["replacements"] == 1
            file.write_text("original")
            for op in ("execute_command", "start_process", "send_input_to_process",
                       "terminate_session", "forget_session"):
                with pytest.raises(PermissionError, match="forbids remote shell"):
                    dispatch(op, {"cwd": str(root), "command": "echo ok"})
        else:
            assert dispatch("execute_command", {"cwd": str(root), "command": "echo ok"})["returncode"] == 0
    assert file.read_text() == "original"


def test_legacy_readonly_has_precedence_over_full(monkeypatch, tmp_path):
    monkeypatch.setenv("DORADO_AGENT_PERMISSION", "full")
    monkeypatch.setenv("DORADO_READ_ONLY", "1")
    assert permission_profile() == "inspect"
    for op in ("write_file", "edit_file", "execute_command", "start_process",
               "send_input_to_process", "terminate_session", "forget_session"):
        with pytest.raises(PermissionError, match="read-only"):
            dispatch(op, {})
    assert dispatch("ping", {})["platform"]
    assert dispatch("list_active_sessions", {}) is not None


def test_profile_validation_and_non_mcp_configuration(monkeypatch):
    for invalid in ("", "admin", "root", "false"):
        monkeypatch.setenv("DORADO_AGENT_PERMISSION", invalid)
        with pytest.raises(ValueError, match="Invalid local"):
            permission_profile()
    monkeypatch.setenv("DORADO_AGENT_PERMISSION", "full")
    assert permission_profile() == "full"
    # Tools cannot pass permission in args to escalate restricted agents.
    monkeypatch.setenv("DORADO_AGENT_PERMISSION", "inspect")
    with pytest.raises(PermissionError):
        dispatch("execute_command", {"permission": "full", "cwd": "/", "command": "true"})


@pytest.mark.parametrize("input_value,expected", [
    ("", "inspect"), ("1", "inspect"), ("2", "files"), ("3", "full"),
])
def test_permission_choice_is_explicit_and_inspection_default(input_value, expected, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: input_value)
    assert ask_permission() == expected


def test_legacy_saved_profile_remains_full_without_env_override(tmp_path, monkeypatch):
    from remote_dorado.agent import main
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    for key in ("DORADO_AGENT_PERMISSION", "DORADO_READ_ONLY"):
        monkeypatch.delenv(key, raising=False)
    save_agent_config({
        "public_url": "https://relay.example.org",
        "agent_token": "x" * 48,
        "device_name": "legacy-pc",
        "auto_shell": False,
    })
    _, _, _, config = resolve_startup_config(argparse.ArgumentParser(), None)
    assert "permission" not in config
    assert permission_profile() == "full"


def test_invalid_saved_profile_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    monkeypatch.delenv("DORADO_AGENT_PERMISSION", raising=False)
    save_agent_config({
        "public_url": "https://relay.example.org",
        "agent_token": "x" * 48,
        "device_name": "pc",
        "permission": "unsafe",
    })
    with pytest.raises(SystemExit):
        resolve_startup_config(argparse.ArgumentParser(), None)
