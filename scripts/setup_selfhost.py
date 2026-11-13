"""Offline self-hosted setup for a FREE Dorado Relay.

Writes only local files. No hosting purchase, network action, Docker start,
credential disclosure, or mutation of an existing installation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets

HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
REQUIRED = ("compose.yaml", "Dockerfile", ".env.example")


def validate_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Domain must be a string")
    domain = value.strip().lower()
    if len(domain) > 253 or not HOST.fullmatch(domain):
        raise ValueError("Use a plain domain such as mcp.example.com, without scheme or path")
    if domain.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError("A publicly resolvable domain is required for remote MCP clients")
    return domain


def _write_new(path: Path, content: str, mode: int) -> None:
    """Never follow/overwrite a symlink or an existing file."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, mode)
    try:
        if os.name != "nt":
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def setup_selfhost(directory: Path, domain: str, *, home: bool = False) -> dict[str, str]:
    domain = validate_host(domain)
    root = directory.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Select a real checked-out repository directory (no symlink)")
    if not all((root / filename).is_file() for filename in REQUIRED):
        raise ValueError("Directory must be the Dorado Relay repository checkout")
    env = root / ".env"
    if home and not (root / "compose.home.yaml").is_file():
        raise ValueError("Home setup requires compose.home.yaml in this checkout")
    caddy = root / ("Caddyfile.home" if home else "Caddyfile.dorado")
    if env.exists() or env.is_symlink():
        raise FileExistsError("Existing .env: setup will not replace your credentials")
    if caddy.exists() or caddy.is_symlink():
        raise FileExistsError(f"Existing {caddy.name}: setup will not overwrite it")

    password = secrets.token_urlsafe(48)
    configuration = (
        "# Generated for the FREE single-owner self-hosted relay.\n"
        f"DORADO_PUBLIC_URL=https://{domain}\n"
        f"DORADO_ADMIN_PASSWORD={password}\n"
        "DORADO_DB=/app/data/dorado.sqlite3\n"
        "DORADO_BIND_HOST=0.0.0.0\n"
        "DORADO_PORT=8765\n"
        "DORADO_MAX_DEVICES=0\n"
        "DORADO_COMMERCIAL_MODE=0\n"
    )
    upstream = "mcp:8765" if home else "127.0.0.1:8765"
    explanation = (
        "# HOME MODE: Caddy runs in the same Docker Compose network as the relay.\n"
        if home else "# Copy this into your Caddy config on the public HTTPS host.\n"
    )
    proxy = (
        explanation
        + f"{domain} {{\n"
        + "    encode zstd gzip\n"
        + f"    reverse_proxy {upstream}\n"
        + "}\n"
    )
    _write_new(env, configuration, 0o600)
    try:
        _write_new(caddy, proxy, 0o644)
    except BaseException:
        env.unlink(missing_ok=True)  # undo half-completed setup
        raise
    return {"env": str(env), "proxy": str(caddy), "domain": domain,
            "mode": "home" if home else "external-proxy"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="Your HTTPS relay hostname, e.g. mcp.example.com")
    parser.add_argument("--home", action="store_true",
                        help="Free home-hosted mode: include Caddy in Docker Compose (ports 80/443)")
    parser.add_argument("--repo-dir", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    domain = args.domain or input("Public relay domain (no https://): ")
    try:
        files = setup_selfhost(args.repo_dir, domain, home=args.home)
    except (ValueError, FileExistsError, OSError) as exc:
        parser.error(str(exc))
    print(f"Free self-hosted setup created for https://{files['domain']}")
    print(f"Private administrator password saved in {files['env']} (mode 0600 on Unix).")
    print("It was not printed or sent anywhere. Keep it in a password manager.")
    print(f"HTTPS reverse-proxy example: {files['proxy']}")
    if args.home:
        print("HOME MODE: No paid host required. Keep this PC running.")
        print("Forward router TCP 80 and TCP 443 to this PC's LAN address.")
        print("Requires a publicly reachable IP; ordinary IPv4 CG-NAT blocks port forwarding.")
        print("Point public DNS or free dynamic DNS to your public IP.")
        print("NEXT: Run Docker Compose with bundled Caddy:")
    else:
        print("NEXT: Configure public DNS and TLS reverse proxy, then use Docker Compose:")
    if os.name == "nt":
        print("  New-Item -ItemType Directory -Force data | Out-Null")
        print("  Windows: confirm Docker Desktop/WSL2 can write to the data volume.")
    else:
        print("  mkdir -p data && sudo chown 10001:10001 data")
        print("  sudo chmod 700 data")
    if args.home:
        print("  docker compose -f compose.yaml -f compose.home.yaml up -d --build")
    else:
        print("  docker compose up -d --build")
    if args.home:
        print("  docker compose -f compose.yaml -f compose.home.yaml ps")
    else:
        print("  docker compose ps")
    print("  curl http://127.0.0.1:8765/health")
    if args.home:
        print("  docker compose -f compose.yaml -f compose.home.yaml logs caddy")
    print(f"  curl https://{files['domain']}/.well-known/oauth-authorization-server")
    print("No processes or services have been started.")


if __name__ == "__main__":
    main()
