"""Free self-hosted onboarding never replaces existing installations or starts servers."""
from pathlib import Path

import pytest

from scripts.setup_selfhost import setup_selfhost, validate_host


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for file in ("Dockerfile", "compose.yaml", ".env.example"):
        (root / file).write_text("placeholder")
    return root


def test_first_run_outputs_private_secret_and_only_local_files(checkout):
    output = setup_selfhost(checkout, "MCP.example.COM")
    assert output["domain"] == "mcp.example.com"
    content = (checkout / ".env").read_text()
    assert "DORADO_PUBLIC_URL=https://mcp.example.com" in content
    assert "DORADO_MAX_DEVICES=0" in content
    assert "DORADO_COMMERCIAL_MODE=0" in content
    assert "DORADO_AGENT_TOKEN" not in content
    assert "DORADO_ENTITLEMENT_MASTER_KEY" not in content
    assert "DORADO_STRIPE_SECRET_KEY" not in content
    secret = next(line.split("=", 1)[1] for line in content.splitlines()
                  if line.startswith("DORADO_ADMIN_PASSWORD="))
    assert len(secret) >= 40
    caddy = (checkout / "Caddyfile.dorado").read_text()
    assert "mcp.example.com" in caddy and "127.0.0.1:8765" in caddy
    assert secret not in caddy
    assert (checkout / ".env").stat().st_mode & 0o077 == 0
    assert len(list(checkout.iterdir())) == 5


def test_rerun_cannot_override_credentials_or_proxy(checkout):
    setup_selfhost(checkout, "first.example.org")
    before = (checkout / ".env").read_bytes()
    with pytest.raises(FileExistsError, match="Existing .env"):
        setup_selfhost(checkout, "second.example.org")
    assert (checkout / ".env").read_bytes() == before


def test_existing_proxy_blocks_entire_setup(checkout):
    (checkout / "Caddyfile.dorado").write_text("customer config")
    with pytest.raises(FileExistsError, match="Existing Caddyfile"):
        setup_selfhost(checkout, "first.example.org")
    assert not (checkout / ".env").exists()


def test_refuses_symlink_destination(checkout, tmp_path):
    target = tmp_path / "important.txt"
    target.write_text("safe")
    (checkout / ".env").symlink_to(target)
    with pytest.raises(FileExistsError):
        setup_selfhost(checkout, "mcp.example.com")
    assert target.read_text() == "safe"


@pytest.mark.parametrize("host", [
    "https://example.org", "example.org/mcp", "localhost", "127.0.0.1",
    "test.example.local", "internal.test", "exa_mple.org", ".example.org",
    "example.org:8765", "example.org?token=x", "-bad.example.org",
    "x" * 64 + ".example.org",
])
def test_reject_bad_hosts(host):
    with pytest.raises(ValueError):
        validate_host(host)


def test_rejects_missing_checkout_and_symlink(checkout, tmp_path):
    with pytest.raises(ValueError, match="checkout"):
        setup_selfhost(tmp_path, "mcp.example.org")
    fake = tmp_path / "linked-repo"
    fake.symlink_to(checkout, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        setup_selfhost(fake, "mcp.example.org")
