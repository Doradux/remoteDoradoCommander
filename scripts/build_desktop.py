"""Build private Windows/Ubuntu desktop candidates; never publish them.

Requires preinstalled PySide6-Essentials, keyring and PyInstaller.
Packages *on the target OS*; a Windows executable cannot be built on Linux.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def build_desktop(*, dist: Path = Path("dist"), work: Path = Path("build")) -> Path:
    root = Path(__file__).resolve().parents[1]
    binary = root / "scripts/desktop_entry.py"
    if not binary.is_file():
        raise FileNotFoundError(binary)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--name", "DoradoRelay",
        "--distpath", str(dist.absolute()),
        "--workpath", str(work.absolute()),
        "--specpath", str(work.absolute()),
        "--paths", str(root),
        "--collect-all", "keyring",
        "--collect-all", "websockets",
        "--collect-all", "certifi",
        "--hidden-import", "keyring.backends.SecretService",
        "--hidden-import", "keyring.backends.Windows",
        str(binary),
    ]
    subprocess.run(cmd, cwd=root, check=True)
    output = dist / "DoradoRelay"
    target = output / ("DoradoRelay.exe" if sys.platform == "win32" else "DoradoRelay")
    if not target.is_file():
        raise RuntimeError("Missing built desktop executable")
    # Dynamic Qt libs must remain replaceable for LGPL compliance; one-dir
    # packaging preserves these rather than embedding them in an EXE.
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--work", type=Path, default=Path("build"))
    args = parser.parse_args()
    result = build_desktop(dist=args.dist, work=args.work)
    print("Built native desktop directory:", result)


if __name__ == "__main__":
    main()
