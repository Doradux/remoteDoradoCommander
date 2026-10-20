import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import tarfile

from remote_dorado.agent import agent_config_path, interactive_setup, read_agent_config, save_agent_config
from scripts.package_release import package, checksum_assets


def test_config_saved_privately_and_overrides(tmp_path, monkeypatch):
    config = tmp_path / 'secure' / 'agent.json'
    monkeypatch.setenv('DORADO_CONFIG_FILE', str(config))
    assert read_agent_config() == {}
    saved = save_agent_config({'public_url': 'https://mcp.example.com', 'agent_token': 't'*48, 'device_name': 'pc-one', 'auto_shell': True})
    assert saved == agent_config_path()
    assert read_agent_config()['device_name'] == 'pc-one'
    assert 't'*48 in config.read_text()
    if os.name != 'nt':
        assert config.stat().st_mode & 0o077 == 0
        assert config.parent.stat().st_mode & 0o077 == 0
    save_agent_config({'device_name': 'pc-two'})
    assert read_agent_config()['device_name'] == 'pc-two'


def test_interactive_config_does_not_echo_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('DORADO_CONFIG_FILE', str(tmp_path / 'agent.json'))
    answers = iter(['my-pc', 'https://mcp.example.com'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    monkeypatch.setattr('remote_dorado.agent.pair_with_relay', lambda url, name: 'secret'*8)
    interactive_setup()
    assert read_agent_config()['device_name'] == 'my-pc'
    assert read_agent_config()['agent_token'] == 'secret'*8
    assert 'secret'*8 not in capsys.readouterr().out


def test_archive_packaging_and_checksum(tmp_path):
    binary = tmp_path / 'dist' / 'remote-dorado-agent'
    binary.parent.mkdir()
    binary.write_bytes(b'agent-test-binary')
    binary.chmod(0o755)
    archive = tmp_path / 'release' / 'remote-dorado-agent-linux-x64.tar.gz'
    package(binary, archive, windows=False)
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ['remote-dorado-agent']
        assert tar.extractfile('remote-dorado-agent').read() == b'agent-test-binary'
        assert tar.getmember('remote-dorado-agent').mode & 0o111
    checks = checksum_assets(tmp_path / 'release').read_text()
    assert archive.name in checks
    binary_exe = binary.with_suffix('.exe')
    binary_exe.write_bytes(b'windows-agent')
    zip_name = tmp_path / 'release' / 'remote-dorado-agent-windows-x64.zip'
    package(binary_exe, zip_name, windows=True)
    with zipfile.ZipFile(zip_name) as z:
        assert z.read('remote-dorado-agent.exe') == b'windows-agent'


def test_install_script_syntax():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'install.sh'
    if sys.platform != 'win32':
        r = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_install_script_with_private_release_fixture(tmp_path):
    """Installer uses gh download, checks SHA256 and installs only binary (no repo)."""
    import hashlib
    import shutil
    if sys.platform != 'linux' or os.uname().machine not in ('x86_64', 'amd64'):
        return
    fixtures = tmp_path / 'assets'
    fixtures.mkdir()
    binary = tmp_path / 'dist' / 'remote-dorado-agent'
    binary.parent.mkdir()
    binary.write_text('#!/bin/sh\necho "remote-dorado-agent-test"\n')
    binary.chmod(0o755)
    archive = fixtures / 'remote-dorado-agent-linux-x64.tar.gz'
    package(binary, archive, windows=False)
    (fixtures / 'SHA256SUMS').write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    mock_bin = tmp_path / 'mock'
    mock_bin.mkdir()
    gh = mock_bin / 'gh'
    gh.write_text('#!/bin/sh\n'
                  'if [ "$1" = auth ] && [ "$2" = status ]; then exit 0; fi\n'
                  'if [ "$1" = release ] && [ "$2" = download ]; then\n'
                  '  while [ "$#" -gt 0 ]; do\n'
                  '    if [ "$1" = "--dir" ]; then shift; cp "${FAKE_ASSETS}"/* "$1"/; exit 0; fi\n'
                  '    shift\n'
                  '  done\n'
                  'fi\nexit 2\n')
    gh.chmod(0o755)
    install_dir = tmp_path / 'installed'
    env = {**os.environ, 'PATH': str(mock_bin) + os.pathsep + os.environ['PATH'],
           'DORADO_INSTALL_DIR': str(install_dir), 'FAKE_ASSETS': str(fixtures)}
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'install.sh'
    proc = subprocess.run(['bash', str(script), 'agent', '--no-setup'], env=env,
                          text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    installed = install_dir / 'remote-dorado-agent'
    assert installed.is_file()
    result = subprocess.run([str(installed)], text=True, capture_output=True)
    assert result.stdout.strip() == 'remote-dorado-agent-test'
