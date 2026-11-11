"""M7 hosts-c: `auoreo ssh import` reads a small, safe subset of an OpenSSH client config."""

from __future__ import annotations

from pathlib import Path

from auoreo.cli.commands import ssh_keys as sk
from auoreo.core import hosts
from auoreo.core.services import Services
from auoreo.core.ssh_config_import import host_name, plan
from tests.unit.test_env_ssh import audit_rows

CONFIG = """
# comment
Host web-1
    HostName 10.0.0.5
    User ubuntu
    Port 2222
    IdentityFile ~/.ssh/id_ed25519

Host bastion
    HostName bastion.example.com
    User jump

Host db
    HostName 10.1.0.9
    User postgres
    ProxyJump bastion

Host *.internal
    User x

Host two names
    HostName 10.0.0.7

Host evil
    HostName 10.0.0.8
    ProxyCommand nc %h %p

Host chain
    HostName 10.0.0.10
    ProxyJump bastion,db

Host meta
    HostName 169.254.169.254

Host outside
    HostName 10.0.0.11
    ProxyJump ops@elsewhere:22

Host 9lives
    HostName=10.0.0.12

Include ~/.ssh/config.d/*
Match host foo
    User bar
"""


def test_plan_reads_the_safe_subset() -> None:
    p = plan(CONFIG, default_user="me")
    got = {e.name: e.fields for e in p.hosts}
    assert got["web_1"] == {"host": "10.0.0.5", "port": 2222, "user": "ubuntu"}
    assert got["bastion"] == {"host": "bastion.example.com", "port": 22, "user": "jump"}
    assert got["db"] == {"host": "10.1.0.9", "port": 22, "user": "postgres", "jump": "bastion"}
    assert got["h_9lives"] == {"host": "10.0.0.12", "port": 22, "user": "me"}
    skipped = dict(p.skipped)
    assert "pattern" in skipped["*.internal"] and "pattern" in skipped["two names"]
    assert "proxycommand" in skipped["evil"]
    assert "multi-hop" in skipped["chain"]
    assert "link-local" in skipped["meta"]
    assert "another Host entry" in skipped["outside"]
    assert any("Include" in n for n in p.notes) and any("Match" in n for n in p.notes)
    assert not any("id_ed25519" in str(e.fields) for e in p.hosts)  # never a key path


def test_names_map_to_the_host_file_pattern() -> None:
    assert host_name("Web-1.Prod") == "web_1_prod" and host_name("9x") == "h_9x" and host_name("--") == "host"
    p = plan("Host a-b\n  HostName h1\nHost a_b\n  HostName h2\n")
    assert [e.name for e in p.hosts] == ["a_b"] and "already used" in dict(p.skipped)["a_b"]


def test_a_jump_to_a_skipped_or_jumping_host_is_skipped() -> None:
    p = plan("Host j\n  ProxyCommand x\nHost t\n  HostName 10.0.0.1\n  ProxyJump j\n")
    assert not p.hosts and "not importable" in dict(p.skipped)["t"]


async def test_cli_import_is_a_dry_run_until_write(services: Services, tmp_path: Path) -> None:
    src = tmp_path / "config"
    src.write_text(CONFIG)
    out: list[str] = []
    ops = sk.Ops(services.settings, services.audit.record, out.append, services.harness)
    assert await sk.import_config(ops, src, default_user="me") == 0
    assert not hosts.read_hosts(services.settings.home)[0] and "dry run" in out[-1]
    assert await sk.import_config(ops, src, key="SSH_KEY_FLEET", write=True, default_user="me") == 0
    found = hosts.read_hosts(services.settings.home)[0]
    assert set(found) == {"web_1", "bastion", "db", "h_9lives"}
    assert found["db"].jump == "bastion" and found["db"].key_secret == "secret:SSH_KEY_FLEET"
    assert not any(hosts.known_keys(services.settings.home, m.host, m.port) for m in found.values())
    row = (await audit_rows(services, "host.imported"))[0]
    assert row.actor == "operator" and row.detail["source"] == "config" and len(row.detail["written"]) == 4
    out.clear()
    await sk.import_config(ops, src, write=True, default_user="me")  # never overwrites
    assert any("exists" in line for line in out)
    assert await sk.import_config(ops, src, key="bad key;x") == sk.EXIT_INVALID
