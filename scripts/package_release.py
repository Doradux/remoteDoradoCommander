"""Package PyInstaller outputs as per-platform GitHub Release assets."""
import argparse
import hashlib
import os
from pathlib import Path
import tarfile
import zipfile


def package(binary: Path, output: Path, *, windows: bool) -> None:
    if not binary.is_file():
        raise FileNotFoundError(binary)
    output.parent.mkdir(parents=True, exist_ok=True)
    if windows:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(binary, arcname=binary.name)
    else:
        with tarfile.open(output, 'w:gz') as archive:
            item = archive.gettarinfo(str(binary), arcname=binary.name)
            item.mode = 0o755
            with binary.open('rb') as stream:
                archive.addfile(item, stream)
    print(f'Created {output} ({output.stat().st_size} bytes)')


def checksum_assets(directory: Path) -> Path:
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    if not files:
        raise RuntimeError(f'No release files in {directory}')
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in files]
    output = directory / 'SHA256SUMS'
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Checksums: {output}')
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['linux-x64', 'windows-x64', 'macos-x64', 'macos-arm64'])
    parser.add_argument('--component', choices=['agent', 'server'])
    parser.add_argument('--dist', default='dist')
    parser.add_argument('--output', default='release-assets')
    parser.add_argument('--checksums', action='store_true')
    opts = parser.parse_args()
    output = Path(opts.output)
    if opts.checksums:
        checksum_assets(output)
    else:
        if not opts.component or not opts.platform:
            parser.error('--platform and --component are required when packaging')
        windows = opts.platform.startswith('windows')
        name = 'remote-dorado-' + opts.component
        extension = '.zip' if windows else '.tar.gz'
        binary = Path(opts.dist) / (name + ('.exe' if windows else ''))
        package(binary, output / (name + '-' + opts.platform + extension), windows=windows)


if __name__ == '__main__':
    main()
