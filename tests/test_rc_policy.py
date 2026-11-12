"""Pre-publication policy: RC jobs cannot release or deploy anything."""
import subprocess
import sys
import tomllib
from pathlib import Path

from remote_dorado.server import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_release_candidate_version_consistent():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["project"]["version"] == APP_VERSION == "0.5.0rc1"


def test_standalone_server_assets_check_does_not_start_network_listener():
    result = subprocess.run(
        [sys.executable, "-m", "remote_dorado.server", "--check-assets"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "assets and license: OK" in result.stdout


def test_rc_workflow_only_creates_test_artifacts():
    workflow = (ROOT / ".github/workflows/rc-build.yml").read_text()
    assert "feature/commercial-hardening" in workflow
    assert "workflow_dispatch:" in workflow
    assert "actions/upload-artifact" not in workflow
    assert "actions/setup-node@v4" in workflow
    assert "macos-15-intel" in workflow
    assert "windows-2022" in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "docker push" not in workflow
    assert "contents: write" not in workflow


def test_dependency_audit_workflow_and_review_doc():
    workflow = (ROOT / ".github/workflows/security-audit.yml").read_text()
    assert "pip-audit" in workflow
    assert "npm audit" in workflow
    assert "pip==26.3" in workflow
    assert (ROOT / "docs/RELEASE_CANDIDATE_REVIEW.md").exists()
