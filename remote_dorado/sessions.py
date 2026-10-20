"""Agent-owned persistent terminal sessions, with bounded output and stable cursors.

Processes are local to the agent. They survive WebSocket reconnects, not agent restarts.
Unix uses a real PTY; Windows uses persistent stdin/stdout subprocess pipes.
"""
from __future__ import annotations

import atexit
from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import signal
import subprocess
import threading
import time
from typing import Any

MAX_SESSIONS = 24
MAX_BUFFER = 1_000_000
MAX_READ = 100_000


@dataclass
class Terminal:
    id: str
    process: subprocess.Popen
    cwd: str
    command: str
    created: float
    pty_fd: int | None = None
    chunks: deque[tuple[int, bytes]] = field(default_factory=deque)
    start_offset: int = 0
    next_offset: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    last_activity: float = field(default_factory=time.time)
    stopped: bool = False

    def append(self, data: bytes) -> None:
        if not data:
            return
        with self.lock:
            self.chunks.append((self.next_offset, data))
            self.next_offset += len(data)
            self.last_activity = time.time()
            while self.chunks and self.next_offset - self.start_offset > MAX_BUFFER:
                old_offset, old = self.chunks.popleft()
                self.start_offset = old_offset + len(old)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "session_id": self.id, "pid": self.process.pid,
                "cwd": self.cwd, "command": self.command,
                "status": "running" if self.process.poll() is None else "exited",
                "returncode": self.process.poll(), "created_at": self.created,
                "last_activity": self.last_activity, "output_cursor": self.next_offset,
                "earliest_cursor": self.start_offset,
                "interactive": True, "owned_by_agent": True,
            }

    def output(self, cursor: int | None, max_bytes: int) -> dict[str, Any]:
        if cursor is not None and (not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0):
            raise ValueError("cursor must be a non-negative integer")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_READ:
            raise ValueError(f"max_bytes must be between 1 and {MAX_READ}")
        with self.lock:
            # No cursor means: read up to MAX_READ newest bytes, useful for a first glance.
            actual = max(self.start_offset, self.next_offset - max_bytes) if cursor is None else max(self.start_offset, cursor)
            requested = actual if cursor is None else cursor
            if actual > self.next_offset:
                raise ValueError("cursor is beyond current output")
            chunks = []
            left = max_bytes
            for start, chunk in self.chunks:
                if start + len(chunk) <= actual:
                    continue
                b = chunk[max(0, actual - start):]
                b = b[:left]
                chunks.append(b)
                left -= len(b)
                if left == 0:
                    break
            output = b"".join(chunks)
            end = actual + len(output)
            return {
                "session_id": self.id,
                "output": output.decode("utf-8", "replace"),
                "cursor": end, "available_cursor": self.next_offset,
                "truncated": requested < self.start_offset or (cursor is None and actual > self.start_offset),
                "has_more": end < self.next_offset,
                "status": "running" if self.process.poll() is None else "exited",
                "returncode": self.process.poll(),
            }


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Terminal] = {}
        self._lock = threading.RLock()

    def _get(self, session_id: str) -> Terminal:
        with self._lock:
            if not isinstance(session_id, str) or session_id not in self._sessions:
                raise KeyError(f"Session '{session_id}' not found on this device")
            return self._sessions[session_id]

    @staticmethod
    def _reader(session: Terminal, stream: Any, *, fd: bool = False) -> None:
        try:
            while True:
                try:
                    data = os.read(stream if fd else stream.fileno(), 8192)
                except OSError:
                    break
                if not data:
                    break
                session.append(data)
        finally:
            if not fd:
                try:
                    stream.close()
                except OSError:
                    pass
            # PTY master is needed for writes until termination.

    def start(self, cwd: Path, command: str | None = None) -> dict[str, Any]:
        if command is not None and (not isinstance(command, str) or len(command) > 8000 or not command.strip()):
            raise ValueError("command must be nonempty and <=8000 characters")
        if not cwd.is_dir():
            raise NotADirectoryError(str(cwd))
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise RuntimeError(f"Maximum {MAX_SESSIONS} sessions. Terminate/forget an old session first.")
            if os.name == "posix":
                master, slave = os.openpty()
                try:
                    if command is None:
                        shell = os.environ.get("SHELL", "/bin/sh")
                        if not Path(shell).is_file():
                            shell = "/bin/sh"
                        argv = [shell, "-i"]
                        label = shell + " -i"
                    else:
                        argv = ["/bin/sh", "-c", command]
                        label = command
                    process = subprocess.Popen(argv, cwd=str(cwd), stdin=slave, stdout=slave, stderr=slave,
                                               start_new_session=True, close_fds=True)
                except BaseException:
                    os.close(master)
                    raise
                finally:
                    os.close(slave)
                terminal = Terminal(secrets.token_hex(12), process, str(cwd), label, time.time(), pty_fd=master)
                reader_targets = [(master, True)]
            else:
                # Windows has no stdlib ConPTY. Persistent CMD pipes support
                # commands and output but not full-screen TUI/ANSI apps.
                if command is None:
                    argv = ["cmd.exe", "/D", "/Q", "/K"]
                    label = "cmd.exe /Q /K"
                else:
                    argv = ["cmd.exe", "/D", "/S", "/C", command]
                    label = command
                process = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, bufsize=0, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                terminal = Terminal(secrets.token_hex(12), process, str(cwd), label, time.time())
                reader_targets = [(process.stdout, False), (process.stderr, False)]
            self._sessions[terminal.id] = terminal
        for stream, fd in reader_targets:
            threading.Thread(target=self._reader, args=(terminal, stream), kwargs={"fd": fd}, daemon=True).start()
        return terminal.summary()

    def list(self) -> dict[str, Any]:
        with self._lock:
            sessions = list(self._sessions.values())
        return {"sessions": sorted((s.summary() for s in sessions), key=lambda s: s["created_at"]), "count": len(sessions)}

    def send(self, session_id: str, input_text: str, append_newline: bool = True, wait_ms: int = 200) -> dict[str, Any]:
        session = self._get(session_id)
        if not isinstance(input_text, str) or len(input_text.encode("utf-8")) > 32_000:
            raise ValueError("input must be UTF-8 text <=32 KB")
        if not isinstance(wait_ms, int) or isinstance(wait_ms, bool) or not 0 <= wait_ms <= 5000:
            raise ValueError("wait_ms must be between 0 and 5000")
        if not isinstance(append_newline, bool):
            raise ValueError("append_newline must be a boolean")
        if session.process.poll() is not None:
            raise RuntimeError("Session exited; start another session")
        data = (input_text + ("\n" if append_newline else "")).encode("utf-8")
        before = session.next_offset
        with session.stdin_lock:
            if session.pty_fd is not None:
                os.write(session.pty_fd, data)
            else:
                if session.process.stdin is None:
                    raise RuntimeError("stdin is closed")
                session.process.stdin.write(data)
                session.process.stdin.flush()
        session.last_activity = time.time()
        if wait_ms:
            time.sleep(wait_ms / 1000)
        output = session.output(before, MAX_READ)
        return {"session_id": session_id, "sent_bytes": len(data), **output}

    def read(self, session_id: str, cursor: int | None = None, max_bytes: int = MAX_READ) -> dict[str, Any]:
        return self._get(session_id).output(cursor, max_bytes)

    def terminate(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        session = self._get(session_id)
        with session.lock:
            if session.process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(session.process.pid, signal.SIGKILL if force else signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    (session.process.kill if force else session.process.terminate)()
            session.stopped = True
        return {"session_id": session_id, "terminated": True, "force": force}

    def forget(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        if session.process.poll() is None:
            raise RuntimeError("Cannot forget a running session; terminate it first")
        with self._lock:
            self._sessions.pop(session_id, None)
        if session.pty_fd is not None:
            try:
                os.close(session.pty_fd)
            except OSError:
                pass
        return {"session_id": session_id, "forgotten": True}

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.process.poll() is None:
                try:
                    self.terminate(session.id, force=True)
                except OSError:
                    pass
            if session.pty_fd is not None:
                try:
                    os.close(session.pty_fd)
                except OSError:
                    pass


sessions = SessionManager()
atexit.register(sessions.close)
