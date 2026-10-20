"""Outgoing-only device agent. Runs all actions as the local OS user."""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import getpass
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

from .sessions import sessions

MAX_OUTPUT = 250_000
DEVICE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


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
    if not DEVICE_NAME_PATTERN.fullmatch(name):
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


def agent_config_path() -> Path:
    """Per-user configuration; no admin/root rights required."""
    if os.environ.get("DORADO_CONFIG_FILE"):
        return Path(os.environ["DORADO_CONFIG_FILE"]).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "remote-dorado-commander" / "agent.json"


def read_agent_config() -> dict[str, Any]:
    path = agent_config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Agent config must be a JSON object")
    return data


def save_agent_config(data: dict[str, Any]) -> Path:
    path = agent_config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    # Atomic replacement avoids half-written credentials on interruption.
    fd, filename = tempfile.mkstemp(prefix=".agent-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        if os.name != "nt":
            os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)
    return path


def suggested_device_name() -> str:
    """Offer a server-compatible hostname without silently choosing it."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", platform.node()).strip("-_")[:64]
    return name or "my-pc"


def ask_device_name(default: str | None = None) -> str:
    """Ask once, validate the alias, then let the caller persist it."""
    default = default if default and DEVICE_NAME_PATTERN.fullmatch(default) else suggested_device_name()
    while True:
        name = input(f"Name for this PC/session [{default}]: ").strip() or default
        if DEVICE_NAME_PATTERN.fullmatch(name):
            return name
        print("Use 1-64 letters (A-Z), numbers, underscores or hyphens.")


def interactive_setup() -> None:
    old = read_agent_config()
    print("Remote Dorado Commander | configure this PC")
    print("Choose an alias for this computer (visible to ChatGPT and other MCP clients).")
    default_name = os.environ.get("DORADO_DEVICE_NAME") or old.get("device_name")
    name = ask_device_name(default_name)
    print("You also need your own HTTPS relay URL and its DORADO_AGENT_TOKEN.")
    default_url = os.environ.get("DORADO_PUBLIC_URL") or old.get("public_url", "")
    raw_url = input(f"Relay URL{f' [{default_url}]' if default_url else ''}: ").strip()
    url = (raw_url or default_url).rstrip("/")
    if not url:
        raise ValueError("A relay URL is required")
    parts = urlsplit(url)
    if not ((parts.scheme == "https" and parts.netloc) or
            (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))):
        raise ValueError("Use https:// for remote access (http://localhost only for local testing)")
    if parts.path.strip("/") or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("Enter the base URL only (for example https://mcp.example.com)")
    existing_token = os.environ.get("DORADO_AGENT_TOKEN") or old.get("agent_token", "")
    token = getpass.getpass("Agent token (input hidden; Enter keeps existing): ").strip() or existing_token
    if len(token) < 32:
        raise ValueError("DORADO_AGENT_TOKEN must be at least 32 characters")
    path = save_agent_config({**old, "public_url": url, "agent_token": token,
                              "device_name": name, "auto_shell": old.get("auto_shell", True)})
    print(f"Saved locally: {path} (the token is not printed)")
    print("Start with: remote-dorado-agent")


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty()


def resolve_startup_config(parser: argparse.ArgumentParser, cli_name: str | None) -> tuple[str, str, str, dict[str, Any]]:
    """On first interactive launch ask for a device alias; never silently use hostname."""
    config = read_agent_config()
    url = os.environ.get("DORADO_PUBLIC_URL") or config.get("public_url", "")
    token = os.environ.get("DORADO_AGENT_TOKEN") or config.get("agent_token", "")
    name = cli_name or os.environ.get("DORADO_DEVICE_NAME") or config.get("device_name", "")

    if not name or not url or len(token) < 32:
        if not is_interactive_terminal():
            parser.error("First launch requires configuration. Run remote-dorado-agent in an interactive terminal "
                         "(or pass DORADO_DEVICE_NAME, DORADO_PUBLIC_URL and DORADO_AGENT_TOKEN for headless use).")
        try:
            if not name and url and len(token) >= 32:
                # Credentials already come from env or the config: only ask for the alias.
                print("Welcome to Remote Dorado Commander! Choose the name for this PC/session.")
                name = ask_device_name()
                config = {**config, "device_name": name, "auto_shell": config.get("auto_shell", True)}
                path = save_agent_config(config)
                print(f"Saved PC name {name!r} to {path}")
            else:
                # First launch with no relay credentials: full wizard, name first.
                interactive_setup()
                config = read_agent_config()
                name = cli_name or os.environ.get("DORADO_DEVICE_NAME") or config.get("device_name", "")
                url = os.environ.get("DORADO_PUBLIC_URL") or config.get("public_url", "")
                token = os.environ.get("DORADO_AGENT_TOKEN") or config.get("agent_token", "")
        except (ValueError, EOFError, KeyboardInterrupt) as exc:
            parser.error(f"Setup cancelled/invalid: {exc}")

    if not isinstance(name, str) or not DEVICE_NAME_PATTERN.fullmatch(name):
        parser.error("Device name must contain 1-64 ASCII letters, digits, underscores or hyphens")
    if not url or len(token) < 32:
        parser.error("Set DORADO_PUBLIC_URL and a random DORADO_AGENT_TOKEN (32+ characters)")
    return url, token, name, config


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect this machine to your private Dorado MCP server")
    parser.add_argument("--name", default=None, help="Optional noninteractive override for the saved PC name")
    parser.add_argument("--auto-shell", action="store_true", help="Start a persistent interactive shell at startup")
    parser.add_argument("--setup", action="store_true", help="Reconfigure this PC (including its saved name)")
    args = parser.parse_args()
    if args.setup:
        try:
            interactive_setup()
        except (ValueError, EOFError, KeyboardInterrupt) as exc:
            parser.error(f"Setup cancelled/invalid: {exc}")
        return
    url, token, name, config = resolve_startup_config(parser, args.name)
    if not os.environ.get("DORADO_ALLOWED_ROOTS") and config.get("allowed_roots"):
        os.environ["DORADO_ALLOWED_ROOTS"] = str(config["allowed_roots"])
    if args.auto_shell or os.environ.get("DORADO_AUTO_SHELL", "0") == "1" or config.get("auto_shell", False):
        created = sessions.start(checked_path(str(Path.home()), must_exist=True))
        print(f"Persistent shell ready: {created['session_id']} ({created['pid']})", flush=True)
    asyncio.run(run_agent(url, token, name))


if __name__ == "__main__":
    main()
