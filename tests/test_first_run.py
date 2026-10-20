"""First-run machine naming and returning launches."""
import argparse
import pytest

from remote_dorado.agent import (
    ask_device_name, read_agent_config, resolve_startup_config, suggested_device_name,
)


def test_first_launch_asks_only_name_when_credentials_exist(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "remote" / "agent.json"
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("DORADO_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.setenv("DORADO_AGENT_TOKEN", "a" * 48)
    monkeypatch.delenv("DORADO_DEVICE_NAME", raising=False)
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: True)
    answers = iter(["office_pc"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    parser = argparse.ArgumentParser()
    assert resolve_startup_config(parser, None)[:3] == ("https://mcp.example.com", "a" * 48, "office_pc")
    assert read_agent_config()["device_name"] == "office_pc"
    assert read_agent_config()["auto_shell"] is True
    assert "a" * 48 not in config_path.read_text()  # env secrets never persisted implicitly
    assert "office_pc" in capsys.readouterr().out

    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Asked again on returning launch"))
    assert resolve_startup_config(parser, None)[2] == "office_pc"


def test_first_launch_full_wizard_name_is_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    for variable in ("DORADO_PUBLIC_URL", "DORADO_AGENT_TOKEN", "DORADO_DEVICE_NAME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: True)
    questions = []
    answers = iter(["my-desktop", "https://mcp.example.com"])
    def answer(prompt):
        questions.append(prompt)
        return next(answers)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("remote_dorado.agent.getpass.getpass", lambda prompt: "b" * 48)
    url, token, name, config = resolve_startup_config(argparse.ArgumentParser(), None)
    assert name == "my-desktop"
    assert url == "https://mcp.example.com"
    assert token == "b" * 48
    assert questions[0].startswith("Name for this PC/session")
    assert questions[1].startswith("Relay URL")
    assert read_agent_config()["device_name"] == name
    assert config["auto_shell"] is True


def test_invalid_device_name_reprompts_with_safe_default(monkeypatch):
    monkeypatch.setattr("remote_dorado.agent.platform.node", lambda: "lab pc.local")
    answers = iter(["not valid!", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert suggested_device_name() == "lab-pc-local"
    assert ask_device_name() == "lab-pc-local"


def test_noninteractive_without_name_errors_instead_of_autonaming(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    monkeypatch.setenv("DORADO_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.setenv("DORADO_AGENT_TOKEN", "x" * 48)
    monkeypatch.delenv("DORADO_DEVICE_NAME", raising=False)
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: False)
    with pytest.raises(SystemExit) as exc:
        resolve_startup_config(argparse.ArgumentParser(), None)
    assert exc.value.code == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_headless_env_override_and_saved_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_CONFIG_FILE", str(tmp_path / "agent.json"))
    monkeypatch.setenv("DORADO_PUBLIC_URL", "https://mcp.example.com")
    monkeypatch.setenv("DORADO_AGENT_TOKEN", "x" * 48)
    monkeypatch.setenv("DORADO_DEVICE_NAME", "ci-runner")
    monkeypatch.setattr("remote_dorado.agent.is_interactive_terminal", lambda: False)
    parser = argparse.ArgumentParser()
    assert resolve_startup_config(parser, None)[2] == "ci-runner"
    assert resolve_startup_config(parser, "cli-override")[2] == "cli-override"


def test_installers_leave_setup_to_first_executable_launch():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert '"$install_dir/$binary" --setup' not in (root / "scripts/install.sh").read_text()
    assert '& $Dest --setup' not in (root / "scripts/install.ps1").read_text()
