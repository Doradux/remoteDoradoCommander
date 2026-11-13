"""Outgoing-only device agent. Runs all actions as the local OS user."""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import httpx
import os
from pathlib import Path
import platform
import re
import tempfile
import sys
import time
import webbrowser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websockets

from .sessions import sessions
from .agent_transport import serve_agent_requests
from .execution import run_bounded_command
from .build_config import DEFAULT_RELAY_URL

MAX_OUTPUT = 250_000
DEVICE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def allowed_roots() -> list[Path]:
    # Trusted devices allow all filesystem paths by default; an explicit value scopes access.
    raw = os.environ.get("DORADO_ALLOWED_ROOTS", "*")
    return [Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part]


def checked_path(value: str, *, must_exist: bool = False) -> Path:
    p = Path(value).expanduser().resolve()
    # Full path access is the default for this owner-controlled agent.
    # Explicit DORADO_ALLOWED_ROOTS values still limit paths; OS permissions always apply.
    if os.environ.get('DORADO_ALLOWED_ROOTS', '*') != '*' and not any(p == root or root in p.parents for root in allowed_roots()):
        raise PermissionError("Path is outside DORADO_ALLOWED_ROOTS")
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


# Local-only capability gate. The MCP caller and relay cannot choose or
# elevate the agent's permission profile. This is NOT an OS sandbox.
PERMISSION_PROFILES = ("inspect", "files", "full")
FILE_OPERATIONS = {"write_file", "edit_file"}
SHELL_OPERATIONS = {
    "execute_command", "start_process", "send_input_to_process",
    "terminate_session", "forget_session",
}
MUTATING_OPERATIONS = FILE_OPERATIONS | SHELL_OPERATIONS


def permission_profile() -> str:
    """Legacy installations remain full; newly paired devices save inspect."""
    if os.environ.get("DORADO_READ_ONLY", "0").strip() == "1":
        return "inspect"
    value = os.environ.get("DORADO_AGENT_PERMISSION", "full").strip().lower()
    if value not in PERMISSION_PROFILES:
        raise ValueError("Invalid local agent permission; use inspect, files or full")
    return value


def read_only_mode() -> bool:
    return permission_profile() == "inspect"


def check_permission(op: str) -> None:
    profile = permission_profile()
    if profile == "inspect" and op in MUTATING_OPERATIONS:
        raise PermissionError("This device is in read-only inspection mode")
    if profile == "files" and op in SHELL_OPERATIONS:
        raise PermissionError("This device forbids remote shell and process control (files profile)")


def dispatch(op: str, args: dict[str, Any]) -> dict[str, Any]:
    # Enforce locally, before any operation side effects.
    check_permission(op)
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
        timeout = int(args.get("timeout_seconds", 30))
        # Shell execution remains powerful, but output and subprocess lifetime
        # are bounded. Use start_process for intentional long-running jobs.
        return run_bounded_command(command, cwd, timeout_seconds=timeout, max_output=MAX_OUTPUT)
    raise ValueError("Unsupported operation")


def websocket_address(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("DORADO_PUBLIC_URL must be a valid http(s) URL")
    return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, "/agent/ws", "", ""))


async def run_agent(url: str, token: str, name: str, *,
                    stop_event: Any = None,
                    on_status: Any = None) -> None:
    """Connect forever unless a GUI stop event is set.

    Emits status on the caller's worker thread. The GUI must relay via Qt
    signals rather than updating widgets from this thread.
    """
    if not DEVICE_NAME_PATTERN.fullmatch(name):
        raise ValueError("Device name must be 1-64 letters, digits, underscores or hyphens")
    validate_relay_url(url)
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Missing device credential")
    def stopping() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    async def pause(seconds: float) -> None:
        elapsed = 0.0
        while elapsed < seconds and not stopping():
            tick = min(.25, seconds - elapsed)
            await asyncio.sleep(tick)
            elapsed += tick

    delay = 1
    while not stopping():
        try:
            if on_status is not None:
                on_status("connecting")
            async with websockets.connect(
                websocket_address(url),
                additional_headers={"Authorization": f"Bearer {token}", "X-Device-Name": name},
                max_size=3_000_000, ping_interval=20, ping_timeout=20,
                open_timeout=10,
            ) as ws:
                print(f"Device {name} connected", flush=True)
                if on_status is not None:
                    on_status("connected")
                delay = 1
                async def close_when_stopped():
                    while not stopping():
                        await asyncio.sleep(.25)
                    await ws.close(code=1000, reason="Desktop app stopped")
                watcher = asyncio.create_task(close_when_stopped()) if stop_event is not None else None
                try:
                    await serve_agent_requests(ws, dispatch)
                finally:
                    if watcher is not None:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            print(f"Disconnected ({exc}); retrying in {delay}s", file=sys.stderr, flush=True)
        finally:
            if on_status is not None:
                on_status("disconnected")
        await pause(delay)
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


def relay_url(config: dict[str, Any]) -> str:
    """Public URL is embedded in release builds (not a secret), never guessed."""
    return (os.environ.get("DORADO_PUBLIC_URL") or config.get("public_url") or
            os.environ.get("DORADO_DEFAULT_RELAY_URL") or DEFAULT_RELAY_URL).rstrip("/")


def validate_relay_url(url: str) -> None:
    parts = urlsplit(url)
    if not ((parts.scheme == "https" and parts.netloc) or
            (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))):
        raise ValueError("A valid HTTPS relay is required (HTTP only for local development)")
    if parts.path.strip("/") or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("Enter only the relay origin, for example https://mcp.example.com")


def pair_with_relay(url: str, name: str, *, open_browser: bool = True,
                    on_pairing: Any = None, stop_event: Any = None) -> str:
    """Request user-confirmed device authorization. Never print or ask for agent tokens."""
    validate_relay_url(url)
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        begin = client.post(url + "/pair/device", json={"device_name": name})
        begin.raise_for_status()
        request = begin.json()
        code = request["device_code"]
        verify = request["verification_uri_complete"]
        # Never blindly trust a link sent by an untrusted relay / compromised response.
        if not verify.startswith(url + "/pair?"):
            raise ValueError("Relay returned an unexpected verification URL")
        print("\nConnect this computer to your private MCP relay:")
        print(f"  {verify}")
        print(f"  Pairing code: {request['user_code']}")
        print("Approve the computer in your browser with your relay administrator password.")
        print("This pairs the device to your MCP relay, not to your ChatGPT login directly.")
        if on_pairing is not None:
            on_pairing(verify, request["user_code"])
        if open_browser and os.environ.get("DORADO_NO_BROWSER") != "1":
            if not webbrowser.open(verify):
                print("Open the link above in a browser to continue.")
        interval = max(3, min(int(request.get("interval", 3)), 30))
        expires_at = time.monotonic() + max(1, min(int(request["expires_in"]), 900))
        while time.monotonic() < expires_at:
            if stop_event is not None and stop_event.wait(interval):
                raise RuntimeError("Pairing canceled by user")
            if stop_event is None:
                time.sleep(interval)
            reply = client.post(url + "/pair/token", json={"device_code": code})
            if reply.status_code == 200:
                issued = reply.json()
                if issued.get("device_name") != name or len(issued.get("agent_token", "")) < 32:
                    raise ValueError("Unexpected pairing response")
                return issued["agent_token"]
            error = reply.json().get("error")
            if reply.status_code == 400 and error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = min(interval + 3, 30)
                continue
            raise RuntimeError(f"Pairing refused: {error or reply.status_code}")
    raise TimeoutError("Pairing expired. Start the agent again for a fresh code.")


def ask_permission(default: str = "inspect") -> str:
    """Explicit local consent for powerful capabilities at setup."""
    if default not in PERMISSION_PROFILES:
        default = "inspect"
    print("Remote access permissions for this device:")
    print("  1. Inspect: list and read files; no changes (recommended)")
    print("  2. Files: inspect and edit files; no remote shell")
    print("  3. Full: file changes, shell commands and persistent processes")
    choices = {"": default, "1": "inspect", "2": "files", "3": "full"}
    while True:
        answer = input(f"Choose 1/2/3 [default: {default}]: ").strip()
        if answer in choices:
            selected = choices[answer]
            print(f"Selected local permission: {selected}")
            return selected
        print("Enter 1, 2 or 3. The relay cannot change this setting remotely.")


def interactive_setup(*, force_pair: bool = False) -> None:
    """First-run wizard: alias + browser approval, no agent token requested."""
    old = read_agent_config()
    print("Remote Dorado Commander | connect this computer")
    name = ask_device_name(os.environ.get("DORADO_DEVICE_NAME") or old.get("device_name"))
    url = relay_url(old)
    if not url:
        print("No relay URL is configured in this release. Your relay administrator must first")
        print("deploy the server and set the DORADO_DEFAULT_RELAY_URL GitHub repository variable.")
        url = input("Your relay URL (one-time setup): ").strip().rstrip("/")
    validate_relay_url(url)
    old_token = old.get("agent_token", "") if old.get("device_name") == name and old.get("public_url") == url else ""
    # New pairing defaults to inspection-only. Existing devices retain their profile.
    chosen_permission = ask_permission(old.get("permission", "inspect"))
    config = {**old, "device_name": name, "public_url": url,
              "auto_shell": old.get("auto_shell", False),
              "permission": chosen_permission}
    # On explicit reconfiguration, rotate credentials by completing a new pairing.
    if force_pair or len(old_token) < 32:
        # Keep the old saved credential until the replacement has actually
        # been authorized; a timeout or aborted pairing must not brick the PC.
        new_token = pair_with_relay(url, name)
        config["agent_token"] = new_token
    else:
        config["agent_token"] = old_token
    path = save_agent_config(config)
    print(f"\n{ name } paired successfully! Stored private config in: {path}")


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty()


def resolve_startup_config(parser: argparse.ArgumentParser, cli_name: str | None) -> tuple[str, str, str, dict[str, Any]]:
    """No URL or token prompts if the release knows its relay; saved identity is reused."""
    config = read_agent_config()
    url = relay_url(config)
    name = cli_name or os.environ.get("DORADO_DEVICE_NAME") or config.get("device_name", "")
    token = os.environ.get("DORADO_AGENT_TOKEN") or config.get("agent_token", "")

    if not name or not url or len(token) < 32:
        if not is_interactive_terminal():
            parser.error("Device not linked. Run remote-dorado-agent in a terminal first, "
                         "or supply DORADO_PUBLIC_URL, DORADO_DEVICE_NAME and DORADO_AGENT_TOKEN for legacy headless use.")
        try:
            if not name and url and len(token) >= 32:
                print("Welcome to Remote Dorado Commander! Choose a name for this computer.")
                name = ask_device_name()
                chosen_permission = ask_permission(config.get("permission", "inspect"))
                config = {**config, "device_name": name, "auto_shell": config.get("auto_shell", False),
                          "permission": chosen_permission}
                save_agent_config(config)  # Explicit env secrets are NOT persisted.
            elif name and url and len(token) < 32:
                validate_relay_url(url)
                print(f"Connecting {name} to your private relay. Approve the pairing in your browser.")
                chosen_permission = ask_permission(config.get("permission", "inspect"))
                token = pair_with_relay(url, name)
                config = {**config, "device_name": name, "public_url": url,
                          "agent_token": token, "auto_shell": config.get("auto_shell", False),
                          "permission": chosen_permission}
                save_agent_config(config)
            else:
                interactive_setup()
                config = read_agent_config()
                name = cli_name or os.environ.get("DORADO_DEVICE_NAME") or config.get("device_name", "")
                url = relay_url(config)
                token = os.environ.get("DORADO_AGENT_TOKEN") or config.get("agent_token", "")
        except (ValueError, EOFError, KeyboardInterrupt, TimeoutError, httpx.HTTPError, RuntimeError) as exc:
            parser.error(f"Pairing failed: {exc}")
    if not isinstance(name, str) or not DEVICE_NAME_PATTERN.fullmatch(name):
        parser.error("Device name must contain 1-64 ASCII letters, digits, underscores or hyphens")
    if not url or len(token) < 32:
        parser.error("Device not linked: complete browser pairing first")
    validate_relay_url(url)
    if "permission" in config and config["permission"] not in PERMISSION_PROFILES:
        parser.error("Invalid saved agent permission: use --setup to select a valid profile")
    return url, token, name, config


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect this machine to your private Dorado MCP relay")
    parser.add_argument("--name", default=None, help="Optional override for the saved PC name")
    parser.add_argument("--auto-shell", action="store_true", help="Start a persistent interactive shell at startup")
    parser.add_argument("--setup", action="store_true", help="Rename/re-link this computer via a browser")
    parser.add_argument("--read-only", action="store_true", help="Force inspection-only (overrides saved profile)")
    parser.add_argument("--permission", choices=PERMISSION_PROFILES, help="Override device permissions for this run only")
    args = parser.parse_args()
    if args.permission:
        os.environ["DORADO_AGENT_PERMISSION"] = args.permission
    if args.read_only:
        os.environ["DORADO_READ_ONLY"] = "1"
    if args.setup:
        try:
            interactive_setup(force_pair=True)
        except (ValueError, EOFError, KeyboardInterrupt, TimeoutError, httpx.HTTPError, RuntimeError) as exc:
            parser.error(f"Pairing failed: {exc}")
        return
    url, token, name, config = resolve_startup_config(parser, args.name)
    if not args.permission and "DORADO_AGENT_PERMISSION" not in os.environ:
        os.environ["DORADO_AGENT_PERMISSION"] = config.get("permission", "full")
    profile = permission_profile()
    print(f"Device permission: {profile} (set on this computer)", flush=True)
    if not os.environ.get("DORADO_ALLOWED_ROOTS") and config.get("allowed_roots"):
        os.environ["DORADO_ALLOWED_ROOTS"] = str(config["allowed_roots"])
    auto_shell = (
        args.auto_shell or os.environ.get("DORADO_AUTO_SHELL", "0") == "1"
        or config.get("auto_shell", False)
    )
    if auto_shell and profile != "full":
        parser.error("Auto-shell requires full local permission; use --setup or --permission full")
    if auto_shell:
        created = sessions.start(checked_path(str(Path.home()), must_exist=True))
        print(f"Persistent shell ready: {created['session_id']} ({created['pid']})", flush=True)
    asyncio.run(run_agent(url, token, name))


if __name__ == "__main__":
    main()
