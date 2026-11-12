"""Safe, offline pilot provisioning: isolated customer configs and no side effects."""
from pathlib import Path
import json
import stat

import pytest

from scripts.provision_pilot import provision


def test_create_separate_customer_configs_without_deploying(tmp_path):
    base = tmp_path / "customers"
    first = provision(base, "acme", "acme.relay.example.com", 19001, seats=2)
    second = provision(base, "zenit", "zenit.relay.example.com", 19002, seats=5)
    assert first != second
    env1 = (first / ".env").read_text()
    env2 = (second / ".env").read_text()
    assert "DORADO_COMMERCIAL_MODE=1" in env1
    assert "DORADO_MAX_DEVICES=2" in env1
    assert "DORADO_MAX_DEVICES=5" in env2
    assert "DORADO_AGENT_TOKEN" not in env1
    assert "DORADO_PUBLIC_URL=https://acme.relay.example.com" in env1
    assert "DORADO_ADMIN_PASSWORD=" in env1
    assert env1 != env2
    assert "127.0.0.1:19001:8765" in (first / "compose.yaml").read_text()
    assert "127.0.0.1:19002:8765" in (second / "compose.yaml").read_text()
    assert "reverse_proxy 127.0.0.1:19001" in (first / "Caddyfile.fragment").read_text()
    assert json.loads((first / "manifest.json").read_text())["deployed"] is False
    if stat.S_ISDIR(base.stat().st_mode):
        assert base.stat().st_mode & 0o077 == 0
    for path in (first, second):
        assert path.stat().st_mode & 0o077 == 0
        for file in path.iterdir():
            assert file.stat().st_mode & 0o077 == 0


def test_reject_duplicate_domain_port_and_customer_without_changing_first(tmp_path):
    base = tmp_path / "customers"
    first = provision(base, "acme", "acme.relay.example.com", 19001)
    original = (first / ".env").read_text()
    with pytest.raises(FileExistsError):
        provision(base, "acme", "other.example.com", 19002)
    with pytest.raises(ValueError, match="Domain"):
        provision(base, "other", "acme.relay.example.com", 19002)
    with pytest.raises(ValueError, match="Port"):
        provision(base, "other", "other.relay.example.com", 19001)
    assert (first / ".env").read_text() == original


@pytest.mark.parametrize("customer,domain,port,seats", [
    ("../escape", "c.relay.example.com", 19001, 2),
    ("unsafe_name", "c.relay.example.com", 19001, 2),
    ("valid", "http://bad.example.com", 19001, 2),
    ("valid", "12.2.3.4", 19001, 2),
    ("valid", "foo.example.com", 80, 2),
    ("valid", "foo.example.com", 19001, 0),
])
def test_invalid_customer_configuration_is_rejected(tmp_path, customer, domain, port, seats):
    with pytest.raises(ValueError):
        provision(tmp_path / "pilot", customer, domain, port, seats)
    assert not (tmp_path / "pilot" / customer).exists()


def test_reject_base_dir_symlink(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual)
    with pytest.raises(ValueError, match="symlink"):
        provision(link, "acme", "acme.relay.example.com", 19001)
