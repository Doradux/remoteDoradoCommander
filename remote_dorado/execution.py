"""Bounded one-shot shell execution for a trusted local agent.

Capture never grows beyond max_output per stream in memory. On timeout,
terminate the entire owned POSIX process group rather than leaving shell
children running. A shell can still access the local OS user's resources;
this is NOT a sandbox or a safe command interpreter.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import threading
from typing import Any

OUTPUT_LIMIT = 250_000


class LimitedCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = bytearray()
        self.truncated = False
        self.thread: threading.Thread | None = None

    def collect(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(16_384)
                if not chunk:
                    break
                available = self.limit - len(self.buffer)
                if available:
                    self.buffer.extend(chunk[:available])
                if len(chunk) > available:
                    self.truncated = True
        except (OSError, ValueError):
            pass

    def value(self) -> str:
        return self.buffer.decode("utf-8", errors="replace")


def _stop_process_tree(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt":
        # Terminate CMD and its child processes (Windows lacks killpg).
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        process.kill()


def run_bounded_command(
    command: str, cwd: Path, *, timeout_seconds: int = 30,
    max_output: int = OUTPUT_LIMIT,
) -> dict[str, Any]:
    if not isinstance(command, str) or not command.strip() or len(command) > 8_000:
        raise ValueError("Empty or oversized command")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise ValueError("Timeout must be an integer")
    timeout = min(max(timeout_seconds, 1), 120)
    if not cwd.is_dir():
        raise NotADirectoryError(str(cwd))
    captures = (LimitedCapture(max_output), LimitedCapture(max_output))
    popen_options: dict[str, Any] = {"start_new_session": True} if os.name == "posix" else {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        command, shell=True, cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, **popen_options,
    )
    assert process.stdout is not None and process.stderr is not None
    try:
        for capture, stream in zip(captures, (process.stdout, process.stderr)):
            capture.thread = threading.Thread(
                target=capture.collect, args=(stream,), daemon=True,
            )
            capture.thread.start()
        try:
            code = process.wait(timeout=timeout)
            expired = False
        except subprocess.TimeoutExpired:
            _stop_process_tree(process)
            process.wait(timeout=8)
            code, expired = None, True
        finally:
            # When a shell finishes but descendants still hold pipes, stop
            # remaining POSIX children. Long-running jobs belong in sessions.
            if os.name == "posix":
                _stop_process_tree(process)
        for stream in (process.stdout, process.stderr):
            stream.close()
        for capture in captures:
            assert capture.thread is not None
            capture.thread.join(timeout=1)
        return {
            "returncode": code, "stdout": captures[0].value(),
            "stderr": captures[1].value(),
            **({"timeout": True} if expired else {
                "truncated": captures[0].truncated or captures[1].truncated,
            }),
        }
    finally:
        if process.poll() is None:
            _stop_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
