"""Portable Qt packaging never drops dynamic shared libraries or license notices."""
import hashlib
from pathlib import Path
import tarfile
import zipfile

import pytest

from scripts.package_desktop import package_desktop


@pytest.fixture
def pretend_binary(tmp_path):
    folder = tmp_path / "DoradoRelay"
    (folder / "_internal").mkdir(parents=True)
    (folder / "DoradoRelay").write_bytes(b"LINUX-EXEC")
    (folder / "DoradoRelay.exe").write_bytes(b"WINDOWS-EXEC")
    (folder / "_internal/Qt6Core.so").write_bytes(b"DYNAMIC-QT-LIB")
    notice = tmp_path / "THIRD_PARTY.md"
    notice.write_text("PySide6 LGPLv3; dynamic Qt libraries can be replaced.")
    return folder, notice


@pytest.mark.parametrize("windows", [True, False])
def test_portable_archive_contains_executable_qt_libraries_and_notice(
    pretend_binary, tmp_path, windows,
):
    folder, notice = pretend_binary
    dest = tmp_path / ("desktop.zip" if windows else "desktop.tar.gz")
    package_desktop(folder, dest, windows=windows, notice=notice)
    assert dest.is_file()
    assert dest.with_name(dest.name + ".sha256").is_file()
    checksum = dest.with_name(dest.name + ".sha256").read_text().split()[0]
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == checksum
    if windows:
        with zipfile.ZipFile(dest) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(dest) as archive:
            names = archive.getnames()
            executable = archive.getmember("DoradoRelay/DoradoRelay")
            assert executable.mode & 0o111
    assert "DoradoRelay/_internal/Qt6Core.so" in names
    assert "DoradoRelay/THIRD_PARTY_NOTICES.md" in names
    assert "DoradoRelay/" + ("DoradoRelay.exe" if windows else "DoradoRelay") in names
    with pytest.raises(FileExistsError):
        package_desktop(folder, dest, windows=windows, notice=notice)


def test_missing_binary_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        package_desktop(
            tmp_path, tmp_path / "bad.zip", windows=True,
            notice=tmp_path / "missing-notice",
        )
