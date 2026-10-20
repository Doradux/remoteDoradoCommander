"""Real terminal tests: interactive shell continuity, output cursors, cleanup."""
import os
import time

import pytest

from remote_dorado.agent import dispatch
from remote_dorado.sessions import MAX_BUFFER, MAX_SESSIONS, SessionManager, Terminal


def await_text(session_id, phrase, cursor=0):
    end = time.monotonic() + 8
    result = None
    while time.monotonic() < end:
        result = dispatch("read_process_output", {"session_id": session_id, "cursor": cursor})
        if phrase in result["output"]:
            return result
        time.sleep(0.04)
    pytest.fail(f"Timed out waiting for {phrase!r}: {result}")


def test_persistent_shell_keeps_cwd_and_output(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(tmp_path))
    info = dispatch("start_process", {"cwd": str(tmp_path)})
    sid = info["session_id"]
    try:
        assert info["status"] == "running"
        assert any(s["session_id"] == sid for s in dispatch("list_active_sessions", {})["sessions"])
        assert dispatch("send_input_to_process", {"session_id": sid, "input": "echo __FIRST__"})["sent_bytes"] > 0
        a = await_text(sid, "__FIRST__")
        cursor = a["cursor"]
        child = tmp_path / "child"
        child.mkdir()
        dispatch("send_input_to_process", {"session_id": sid, "input": "cd child"})
        dispatch("send_input_to_process", {"session_id": sid, "input": "pwd; echo __SECOND__"})
        b = await_text(sid, "__SECOND__", cursor)
        assert str(child) in b["output"]
        reread = dispatch("read_process_output", {"session_id": sid, "cursor": cursor})
        assert "__SECOND__" in reread["output"]  # non-destructive reading
        with pytest.raises(RuntimeError):
            dispatch("forget_session", {"session_id": sid})
    finally:
        dispatch("terminate_session", {"session_id": sid, "force": True})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and dispatch("list_active_sessions", {})["sessions"] and any(s["session_id"] == sid and s["status"] == "running" for s in dispatch("list_active_sessions", {})["sessions"]):
            time.sleep(.05)
        dispatch("forget_session", {"session_id": sid})


def test_long_running_process_survives_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(tmp_path))
    info = dispatch("start_process", {"cwd": str(tmp_path), "command": 'echo START; sleep 1; echo DONE'})
    sid = info["session_id"]
    try:
        await_text(sid, "START")
        assert dispatch("list_active_sessions", {})["count"] >= 1
        assert "DONE" in await_text(sid, "DONE")["output"]
    finally:
        dispatch("terminate_session", {"session_id": sid, "force": True})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if next(x for x in dispatch("list_active_sessions", {})["sessions"] if x["session_id"] == sid)["status"] == "exited":
                break
            time.sleep(.05)
        dispatch("forget_session", {"session_id": sid})


def test_own_sessions_only_and_output_cursor():
    manager = SessionManager()
    with pytest.raises(KeyError):
        manager.read("external-terminal-id")
    class MockProcess:
        pid = 100
        def poll(self): return None
    term = Terminal("abc", MockProcess(), "/tmp", "sh", time.time())
    term.append(b"a" * (MAX_BUFFER - 1))
    term.append(b"b" * 64)
    assert term.start_offset > 0
    value = term.output(0, 100)
    assert value["truncated"] is True
    assert value["cursor"] > 0
    with pytest.raises(ValueError):
        term.output(-1, 100)
    with pytest.raises(ValueError):
        term.output(0, 100001)


@pytest.mark.skipif(os.name != "posix", reason="PTY test requires POSIX")
def test_interactive_pty(tmp_path, monkeypatch):
    monkeypatch.setenv("DORADO_ALLOWED_ROOTS", str(tmp_path))
    info = dispatch("start_process", {"cwd": str(tmp_path)})
    try:
        dispatch("send_input_to_process", {"session_id": info["session_id"], "input": "printf 'PTY_OK\\n'"})
        assert "PTY_OK" in await_text(info["session_id"], "PTY_OK")["output"]
    finally:
        dispatch("terminate_session", {"session_id": info["session_id"], "force": True})
        time.sleep(.05)
        dispatch("forget_session", {"session_id": info["session_id"]})
