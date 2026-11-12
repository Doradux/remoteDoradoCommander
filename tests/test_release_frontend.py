"""Release pipeline must ship the compiled Animate UI frontend, not a bare relay."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_builds_frontend_before_linux_executable_and_wheel():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert release.count("npm ci --prefix frontend --no-audit --no-fund") == 3
    assert release.count("npm run build --prefix frontend") == 3
    assert "--collect-data remote_dorado scripts/server_entry.py" in release
    assert "git diff --exit-code -- remote_dorado/static" in release
    assert "if: matrix.platform == 'linux-x64'" in release


def test_selfhost_is_public_and_not_tied_to_cloud():
    readme = (ROOT / "README.md").read_text()
    setup = (ROOT / "scripts/setup_selfhost.py").read_text()
    assert "scripts/setup_selfhost.py" in readme
    assert "DORADO_COMMERCIAL_MODE=0" in setup
    assert "DORADO_MAX_DEVICES=0" in setup
    assert "DORADO_ENTITLEMENT_MASTER_KEY=" not in setup
    assert "Stripe" not in setup
