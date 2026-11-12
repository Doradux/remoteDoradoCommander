"""Agensi ZIP static review: only text, no secrets or paid upsells."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.verify_agensi_skill import check_skill_zip

GOOD = (
    "---\n"
    "name: dorado-relay-remote-dev\n"
    "description: Safe workflows for owner-controlled remote MCP development devices.\n"
    "---\n"
    "# Workflow\n"
    "Inspect first, then ask the owner before changing files.\n"
)


def make_zip(path: Path, entries: dict[str, str]):
    with ZipFile(path, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path


def test_valid_standalone_skill(tmp_path):
    path = make_zip(tmp_path / "good.zip", {
        "SKILL.md": GOOD, "references/security.md": "No unsafe instructions.",
    })
    result = check_skill_zip(path)
    assert result["name"] == "dorado-relay-remote-dev"
    assert result["files"] == 2


@pytest.mark.parametrize("name,text", [
    ("SKILL.md", GOOD.replace("name: dorado-relay-remote-dev", "name: INVALID Name")),
    ("SKILL.md", GOOD + "\n-----BEGIN PRIVATE KEY-----\n"),
    ("SKILL.md", GOOD + "\nhttps://stripe.com/checkout/abc\n"),
    ("SKILL.md", GOOD + "\nDORADO_ADMIN_PASSWORD=SecretValue\n"),
])
def test_frontmatter_or_sensitive_payload_rejected(tmp_path, name, text):
    path = make_zip(tmp_path / "bad.zip", {name: text})
    with pytest.raises(ValueError):
        check_skill_zip(path)


@pytest.mark.parametrize("entry", [
    "../outside.md", "payload.exe", "/absolute.md", "nested/../../unsafe.md",
])
def test_unsafe_zip_layout_rejected(tmp_path, entry):
    path = make_zip(tmp_path / "bad.zip", {"SKILL.md": GOOD, entry: "payload"})
    with pytest.raises(ValueError):
        check_skill_zip(path)
