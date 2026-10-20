"""Outgoing-only device agent. Runs all actions as the local OS user."""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

from .sessions import sessions

MAX_OUTPUT = 250_000


def allowed_roots() -> list[Path]:
    raw = os.environ.get("DORADO_ALLOWED_ROOTS", str(Path.home()))
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part]


def checked_path(value: str, *, must_exist: bool = False) -> Path:
    p = Path(value).expanduser().resolve()
    if not any(p == root or root in p.parents for root in allowed_roots()):
        raise PermissionError("Path is outside DORADO_ALLOWED_ROOTS")
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


def dispatch(op: str, args: dict[str, Any]) -> dict[str, Any]:
    if op == "list_active_sessions":
        return sessions.list()
    if op == "start_process":
        cwd = checked_path(str(args.get("cwd", str(Path.home()))), must_exist=True)
        return sessions.start(cwd, args.get("command"))
    if op == "send_input_to_process":
        return sessions.send(str(args["session_id"]), args["input"], args.get("append_newline", True), args.get("wait_ms", 200))
    if op == "read_process_output":
        return sessions.read(str(args["session_id"]), args.get("cursor"), args.get("max_bytes", 100_000))
    if op == "terminate_session":
        return sessions.terminate(str(args["session_id"]), force=args.get("force", False))
    if op == "forget_session":
        return sessions.forget(str(args["session_id"]))
    if op == "ping":
        return {"hostname": platform.node(), "platform": platform.system(), "time": time.time()}
    if op == "list_directory":
        p = checked_path(str(args["path"]), must_exist=True)
        if not p.is_dir():
            raise NotADirectoryError(str(p))
        limit = min(max(int(args.get("limit", 200)), 1), 1000)
        return {"entries": [{"name": x.name, "type": "directory" if x.is_dir() else "file", "size": x.stat().st_size if x.is_file() else None} for x in sorted(p.iterdir(), key=lambda x: x.name.lower())][:limit]}
    if op == "read_file":
        p = checked_path(str(args["path"]), must_exist=True)
        limit = min(max(int(args.get("max_chars", 100_000)), 1), 250_000)
        with p.open("r", encoding="utf-8", errors="replace") as stream:
            return {"content": stream.read(limit), "truncated": stream.read(1) != ""}
    if op == "write_file":
        p = checked_path(str(args["path"]))
        content = str(args["content"])
        if len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("Content exceeds 2 MB")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w" if args.get("overwrite", False) else "x", encoding="utf-8") as stream:
            stream.write(content)
        return {"path": str(p), "bytes": len(content.encode("utf-8"))}
    if op == "edit_file":
        p = checked_path(str(args["path"]), must_exist=True)
        original = p.read_text(encoding="utf-8")
        old, new = str(args["old_text"]), str(args["new_text"])
        if not old or original.count(old) != 1:
            raise ValueError("old_text must match exactly once")
        p.write_text(original.replace(old, new, 1), encoding="utf-8")
        return {"path": str(p), "replacements": 1}
    if op == "search_files":
        p = checked_path(str(args["path"]), must_exist=True)
        pattern = str(args.get("pattern", "*"))
        limit = min(max(int(args.get("limit", 100)), 1), 500)
        matches = []
        for root, dirs, files in os.walk(p, followlinks=False):
            safe_dirs = []
            for d in dirs:
                try:
                    if checked_path(str(Path(root) / d)).is_dir():
                        safe_dirs.append(d)
                except PermissionError:
                    continue
            dirs[:] = safe_dirs
            for name in dirs + files:
                candidate = Path(root) / name
                if fnmatch.fnmatch(name, pattern):
                    matches.append(str(candidate))
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}
    if op == "execute_command":
        cwd = checked_path(str(args["cwd"]), must_exist=True)
        if not cwd.is_dir():
            raise NotADirectoryError(str(cwd))
        command = str(args["command"])
        if not command.strip() or len(command) > 8_000:
            raise ValueError("Empty or oversized command")
        timeout = min(max(int(args.get("timeout_seconds", 30)), 1), 120)
        # Intentional arbitrary shell access. Only authorize trusted MCP clients.
        try:
            completed = subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, timeout=timeout)
            return {"returncode": completed.returncode,
                    "stdout": completed.stdout[:MAX_OUTPUT].decode("utf-8", errors="replace"),
                    "stderr": completed.stderr[:MAX_OUTPUT].decode("utf-8", errors="replace"),
                    "truncated": len(completed.stdout) > MAX_OUTPUT or len(completed.stderr) > MAX_OUTPUT}
        except subprocess.TimeoutExpired as exc:
            return {"returncode": None, "timeout": True, "stdout": (exc.stdout or b"")[:MAX_OUTPUT].decode("utf-8", errors="replace"), "stderr": (exc.stderr or b"")[:MAX_OUTPUT].decode("utf-8", errors="replace")}
    raise ValueError("Unsupported operation")


def websocket_address(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("DORADO_PUBLIC_URL must be a valid http(s) URL")
    return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, "/agent/ws", "", ""))


async def run_agent(url: str, token: str, name: str) -> None:
    if not name or len(name) > 64 or not all(c.isalnum() or c in "_-" for c in name):
        raise ValueError("Device name must be 1-64 letters, digits, underscores or hyphens")
    delay = 1
    while True:
        try:
            async with websockets.connect(websocket_address(url), additional_headers={"Authorization": f"Bearer {token}", "X-Device-Name": name}, max_size=3_000_000, ping_interval=20, ping_timeout=20) as ws:
                print(f"Device {name} connected", flush=True)
                delay = 1
                send_lock = asyncio.Lock()
                async for msg in ws:
                    request = json.loads(msg)
                    if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                        continue
                    async def handle(item: dict[str, Any]) -> None:
                        try:
                            result = await asyncio.to_thread(dispatch, str(item["op"]), item.get("args", {}))
                            reply = {"id": item["id"], "result": result}
                        except Exception as exc:
                            reply = {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}
                        async with send_lock:
                            await ws.send(json.dumps(reply))
                    asyncio.create_task(handle(request))
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            print(f"Disconnected ({exc}); retrying in {delay}s", file=sys.stderr, flush=True)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect this machine to your private Dorado MCP server")
    parser.add_argument("--name", default=os.environ.get("DORADO_DEVICE_NAME", platform.node()))
    parser.add_argument("--auto-shell", action="store_true", help="Start a persistent interactive shell at startup")
    args = parser.parse_args()
    url, token = os.environ.get("DORADO_PUBLIC_URL", ""), os.environ.get("DORADO_AGENT_TOKEN", "")
    if not url or len(token) < 32:
        parser.error("Set DORADO_PUBLIC_URL and a random DORADO_AGENT_TOKEN (32+ characters)")
    if args.auto_shell or os.environ.get("DORADO_AUTO_SHELL", "0") == "1":
        created = sessions.start(checked_path(str(Path.home()), must_exist=True))
        print(f"Persistent shell ready: {created['session_id']} ({created['pid']})", flush=True)
    asyncio.run(run_agent(url, token, args.name))


if __name__ == "__main__":
    main()
