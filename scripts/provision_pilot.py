"""Generate isolated pilot-relay configurations, without deploying anything.

For a small, manual, one-customer-per-container pilot ONLY. Not a
multi-tenant SaaS, not a payment system, and not a self-service interface.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

SLUG = re.compile(r"[a-z][a-z0-9-]{1,38}[a-z0-9]\Z")
FQDN = re.compile(
    r"(?=.{4,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z"
)
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,240}\Z")


def _require_plain_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("Expected directory")
    # Customer credentials must not be world-accessible, even with a permissive umask.
    path.chmod(0o700)


def _write_private(path: Path, content: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(content)


def provision(
    base_dir: Path, customer_id: str, domain: str, port: int,
    seats: int = 2, image: str = "dorado-relay:pilot"
) -> Path:
    """Create config atomically. Never print or return secrets."""
    domain = domain.lower().strip()
    if not SLUG.fullmatch(customer_id):
        raise ValueError("customer_id must contain 3-40 lower-case letters, digits and hyphens")
    if not FQDN.fullmatch(domain):
        raise ValueError("domain must be a real-looking HTTPS hostname, not an IP address")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    if not isinstance(seats, int) or not 1 <= seats <= 100:
        raise ValueError("seats must be between 1 and 100")
    if not IMAGE.fullmatch(image):
        raise ValueError("Invalid image reference")
    _require_plain_directory(base_dir)
    output_dir = base_dir / customer_id
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Customer already exists; refusing to replace it")
    for sibling in base_dir.iterdir():
        if not sibling.is_dir() or sibling.is_symlink():
            continue
        manifest = sibling / "manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("domain") == domain:
                raise ValueError("Domain already allocated to another customer")
            if data.get("port") == port:
                raise ValueError("Port already allocated to another customer")

    stage = Path(tempfile.mkdtemp(prefix=".pilot-", dir=base_dir))
    stage.chmod(0o700)
    try:
        password = secrets.token_urlsafe(48)
        env = (
            f"DORADO_PUBLIC_URL=https://{domain}\n"
            f"DORADO_ADMIN_PASSWORD={password}\n"
            "DORADO_DB=/app/data/dorado.sqlite3\n"
            "DORADO_BIND_HOST=0.0.0.0\n"
            "DORADO_PORT=8765\n"
            f"DORADO_MAX_DEVICES={seats}\n"
            "DORADO_COMMERCIAL_MODE=1\n"
        )
        compose = (
            "services:\n"
            "  relay:\n"
            f"    image: {image}\n"
            "    restart: unless-stopped\n"
            "    env_file: [.env]\n"
            f"    ports: ['127.0.0.1:{port}:8765']\n"
            "    read_only: true\n"
            "    tmpfs: ['/tmp:size=16m,mode=1777']\n"
            "    security_opt: ['no-new-privileges:true']\n"
            "    cap_drop: [ALL]\n"
            "    pids_limit: 64\n"
            "    mem_limit: 256m\n"
            "    cpus: '0.50'\n"
            "    volumes: ['data:/app/data']\n"
            "volumes:\n"
            "  data:\n"
        )
        caddy = (
            f"{domain} {{\n"
            "  encode zstd gzip\n"
            f"  reverse_proxy 127.0.0.1:{port}\n"
            "}\n"
        )
        manifest = {
            "customer_id": customer_id,
            "domain": domain,
            "port": port,
            "seats": seats,
            "image": image,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "kind": "isolated_single_customer_relay",
            "deployed": False,
        }
        _write_private(stage / ".env", env)
        _write_private(stage / "compose.yaml", compose)
        _write_private(stage / "Caddyfile.fragment", caddy)
        _write_private(stage / "manifest.json", json.dumps(manifest, indent=2) + "\n")
        # Atomic publish; no half-built customer if something above failed.
        stage.rename(output_dir)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, type=Path)
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seats", type=int, default=2)
    parser.add_argument("--image", default="dorado-relay:pilot")
    args = parser.parse_args()
    folder = provision(args.base_dir, args.customer_id, args.domain, args.port, args.seats, args.image)
    print(f"Provisioned draft configuration in {folder}; nothing has been deployed.")
    print("Customer password is ONLY in the .env file (mode 0600); never share it in chat.")


if __name__ == "__main__":
    main()
