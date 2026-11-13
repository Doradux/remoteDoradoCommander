"""Free at-home relay with bundled HTTPS proxy and no paid server."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from scripts.setup_selfhost import setup_selfhost


@pytest.fixture
def home_checkout(tmp_path):
    root = tmp_path / "dorado-home"
    root.mkdir()
    project = Path(__file__).resolve().parents[1]
    for name in ("compose.yaml", "compose.home.yaml", "Dockerfile", ".env.example"):
        (root / name).write_text((project / name).read_text())
    return root


def test_home_mode_creates_matching_caddy_and_private_credentials(home_checkout):
    result = setup_selfhost(home_checkout, "HOME.duckdns.org", home=True)
    assert result["mode"] == "home"
    assert result["domain"] == "home.duckdns.org"
    env = (home_checkout / ".env").read_text()
    proxy = (home_checkout / "Caddyfile.home").read_text()
    assert "DORADO_PUBLIC_URL=https://home.duckdns.org" in env
    assert "DORADO_MAX_DEVICES=0" in env
    assert "DORADO_COMMERCIAL_MODE=0" in env
    assert "home.duckdns.org {" in proxy
    assert "reverse_proxy mcp:8765" in proxy
    assert "127.0.0.1:8765" not in proxy
    assert "DORADO_ADMIN_PASSWORD" not in proxy
    assert not (home_checkout / "Caddyfile.dorado").exists()
    if os.name == "posix":
        assert (home_checkout / ".env").stat().st_mode & 0o077 == 0


def test_home_mode_cannot_replace_own_generated_files(home_checkout):
    setup_selfhost(home_checkout, "first.duckdns.org", home=True)
    before = (home_checkout / ".env").read_bytes()
    with pytest.raises(FileExistsError, match="Existing .env"):
        setup_selfhost(home_checkout, "second.duckdns.org", home=True)
    assert (home_checkout / ".env").read_bytes() == before


def test_existing_home_caddy_blocks_setup_without_creating_env(home_checkout):
    (home_checkout / "Caddyfile.home").write_text("do not touch")
    with pytest.raises(FileExistsError, match="Existing Caddyfile.home"):
        setup_selfhost(home_checkout, "home.duckdns.org", home=True)
    assert not (home_checkout / ".env").exists()
    assert (home_checkout / "Caddyfile.home").read_text() == "do not touch"


def test_home_requires_compose_overlay_before_writing(home_checkout):
    (home_checkout / "compose.home.yaml").unlink()
    with pytest.raises(ValueError, match="compose.home.yaml"):
        setup_selfhost(home_checkout, "home.duckdns.org", home=True)
    assert not (home_checkout / ".env").exists()


def test_home_cli_runs_offline_and_never_exposes_secret(home_checkout):
    cmd = [
        sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/setup_selfhost.py"),
        "--home", "--domain", "personal.duckdns.org",
        "--repo-dir", str(home_checkout),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "docker compose -f compose.yaml -f compose.home.yaml" in result.stdout
    assert "Forward router TCP 80 and TCP 443" in result.stdout
    secret = next(line.partition("=")[2] for line in (home_checkout / ".env").read_text().splitlines()
                  if line.startswith("DORADO_ADMIN_PASSWORD="))
    assert len(secret) >= 40
    assert secret not in result.stdout
    assert not (home_checkout / "data").exists()


def test_home_compose_keeps_relay_private_and_exposes_only_tls_gateway():
    import yaml
    root = Path(__file__).resolve().parents[1]
    core = yaml.safe_load((root / "compose.yaml").read_text())
    extra = yaml.safe_load((root / "compose.home.yaml").read_text())
    assert core["services"]["mcp"]["ports"] == ["127.0.0.1:8765:8765"]
    proxy = extra["services"]["caddy"]
    assert proxy["ports"] == ["80:80", "443:443"]
    assert "./Caddyfile.home:/etc/caddy/Caddyfile:ro" in proxy["volumes"]
    assert "caddy_home_data:/data" in proxy["volumes"]
    assert "caddy_home_config:/config" in proxy["volumes"]
    assert "mcp" in proxy["depends_on"]
    assert not proxy.get("privileged", False)


def test_home_guide_explicitly_avoids_paid_hosting():
    root = Path(__file__).resolve().parents[1]
    guide = (root / "docs/HOME_SELF_HOSTED.md").read_text()
    assert "No necesitas comprar un VPS" in guide
    assert "CG-NAT" in guide
    assert "80" in guide and "443" in guide
    assert "propio PC" in guide
    assert "duerme" in guide
