"""Offline preflight for a self-contained Agensi skill ZIP.

Conservative static checks only, not Agensi's own security review.
Never uploads the ZIP or touches accounts.
"""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile

MAX_SKILL_ZIP = 50 * 1024 * 1024
MAX_UNCOMPRESSED = 5 * 1024 * 1024
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
BLOCKED = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:sk_live_|whsec_|ghp_|github_pat_|xoxb-)[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)(?:DORADO_ADMIN_PASSWORD|DORADO_AGENT_TOKEN)\s*=\s*[^\s<#]+"),
    re.compile(r"(?i)(?:gumroad\.com|lemonsqueezy\.com|stripe\.com/(?:pay|checkout)|ko-fi\.com)"),
]


def check_skill_zip(path: Path) -> dict[str, str | int]:
    if not path.is_file() or path.stat().st_size > MAX_SKILL_ZIP:
        raise ValueError("Missing or oversized ZIP")
    files: dict[str, str] = {}
    size = 0
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) < 1 or len(entries) > 100:
            raise ValueError("Invalid number of archive entries")
        for item in entries:
            name = item.filename
            safe = PurePosixPath(name)
            if (item.is_dir() or safe.is_absolute() or
                    ".." in safe.parts or "\\" in name or
                    len(safe.parts) > 4 or not name.endswith(".md")):
                raise ValueError("Only safe Markdown files are accepted")
            if name in files:
                raise ValueError("Duplicate ZIP entry")
            size += item.file_size
            if size > MAX_UNCOMPRESSED:
                raise ValueError("Uncompressed content is too large")
            data = archive.read(item)
            if b"\x00" in data:
                raise ValueError("Binary payload detected")
            content = data.decode("utf-8", errors="strict")
            files[name] = content
            for pattern in BLOCKED:
                if pattern.search(content):
                    raise ValueError(f"Potential secret or off-market checkout in {name}")
    skills = [name for name in files if PurePosixPath(name).name == "SKILL.md"]
    if len(skills) != 1:
        raise ValueError("Expected exactly one SKILL.md")
    main = files[skills[0]]
    if not main.startswith("---\n") or "\n---\n" not in main[4:]:
        raise ValueError("SKILL.md must have YAML frontmatter")
    front = main.split("\n---\n", 1)[0][4:]
    fields = dict(
        line.split(": ", 1) for line in front.splitlines()
        if ": " in line
    )
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME.fullmatch(name):
        raise ValueError("Skill frontmatter name must be a lowercase hyphenated slug")
    if not 15 <= len(description) <= 1200:
        raise ValueError("Skill frontmatter needs a substantive description")
    if any("node_modules" in file or "__pycache__" in file for file in files):
        raise ValueError("Disallowed build directories")
    return {"name": name, "files": len(files), "bytes": size}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", type=Path)
    args = parser.parse_args()
    result = check_skill_zip(args.zip_path)
    print("Offline Agensi static preflight: PASS")
    print(f"Skill: {result['name']}; files: {result['files']}; bytes: {result['bytes']}")
    print("Still requires real Agensi automated and manual review.")


if __name__ == "__main__":
    main()
