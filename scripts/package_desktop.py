"""Package an onedir Qt desktop build with third-party license notices."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tarfile
import zipfile


def package_desktop(
    binary_folder: Path, destination: Path, *,
    windows: bool,
    notice: Path,
) -> Path:
    if not binary_folder.is_dir():
        raise FileNotFoundError(binary_folder)
    executable = binary_folder / ("DoradoRelay.exe" if windows else "DoradoRelay")
    if not executable.is_file():
        raise FileNotFoundError(executable)
    if not notice.is_file():
        raise FileNotFoundError(notice)
    members = sorted(p for p in binary_folder.rglob("*") if p.is_file())
    if not members or len(members) > 2000:
        raise ValueError("Unexpected desktop distribution contents")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("Will not overwrite an existing desktop candidate")
    prefix = "DoradoRelay"
    if windows:
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for item in members:
                archive.write(item, arcname=prefix + "/" + str(item.relative_to(binary_folder)).replace("\\", "/"))
            archive.write(notice, arcname=prefix + "/THIRD_PARTY_NOTICES.md")
    else:
        with tarfile.open(destination, "x:gz") as archive:
            for item in members:
                entry = archive.gettarinfo(str(item), arcname=prefix + "/" + str(item.relative_to(binary_folder)))
                if item == executable:
                    entry.mode |= 0o111
                with item.open("rb") as stream:
                    archive.addfile(entry, stream)
            archive.add(notice, arcname=prefix + "/THIRD_PARTY_NOTICES.md")
    checksum = destination.with_name(destination.name + ".sha256")
    checksum.write_text(
        hashlib.sha256(destination.read_bytes()).hexdigest() + "  " + destination.name + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows-x64", "ubuntu-x64"), required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist/DoradoRelay"))
    parser.add_argument("--output-dir", type=Path, default=Path("desktop-artifacts"))
    opts = parser.parse_args()
    win = opts.platform == "windows-x64"
    dest = opts.output_dir / ("DoradoRelay-Desktop-" + opts.platform + (".zip" if win else ".tar.gz"))
    result = package_desktop(
        opts.dist, dest, windows=win,
        notice=Path(__file__).resolve().parents[1] / "docs/DESKTOP_THIRD_PARTY.md",
    )
    print("Candidate:", result)
    print("SHA256:", result.with_name(result.name + ".sha256"))


if __name__ == "__main__":
    main()
