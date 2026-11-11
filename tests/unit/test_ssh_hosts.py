"""`hosts/<name>.yaml` inventory (M7 hosts-a): manifest, runtime-root-only loading, the host
files and `known_hosts` under `<home>`, and the `auoreo ssh add|list` commands. Everything
here runs without a network; the connecting paths are in `test_env_ssh.py`."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from auoreo.core import hosts
from auoreo.core.permissions import SENSITIVE_WRITE_GLOBS, PermissionEngine, Rule
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.manifests import RESERVED_NAMES
from auoreo.harness.manifests_hosts import HostManifest
from auoreo.harness.registry import Harness

GOOD = {"name": "lab", "host": "10.0.0.5", "user": "ubuntu", "key_secret": "secret:SSH_KEY_LAB"}
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINsODwY14lVJHbIWemgeTyJYQTn4CwG5ST0SZm8pBl78"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake"


def host_yaml(name: str = "lab", **over: object) -> str:
    return json.dumps({**GOOD, "name": name, **over})  # JSON is YAML


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ manifest
def test_manifest_defaults_and_forbidden_keys() -> None:
    m = HostManifest(**GOOD)
    assert (m.port, m.keepalive_interval_s) == (22, 30)
    for extra in ("private_key", "public_key", "fingerprint", "password", "jump", "tags"):
        with pytest.raises(ValidationError):
            HostManifest(**GOOD, **{extra: "x"})
    assert set(HostManifest.model_fields) == {
        "name", "host", "port", "user", "key_secret", "keepalive_interval_s",
    }  # fmt: skip


@pytest.mark.parametrize("name", ["con", "CON", "Com1", "lpt9", "nul", "aux", "prn"])
def test_reserved_names_rejected_in_any_case(name: str) -> None:
    assert name.lower() in RESERVED_NAMES
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, "name": name})


@pytest.mark.parametrize("name", ["../x", "a/b", "a.b", "a-b", "", "x" * 65, "a\\b", "Lab"])
def test_names_cannot_traverse(name: str) -> None:
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, "name": name})


@pytest.mark.parametrize(
    "field,value",
    [
        ("key_secret", "/home/u/.ssh/id_ed25519"),
        ("key_secret", "-----BEGIN OPENSSH PRIVATE KEY-----"),
        ("key_secret", "SSH_KEY_LAB"),
        ("host", "a b"),
        ("host", "-oProxyCommand=x"),
        ("host", "u@h"),
        ("host", "h\nx"),
        ("user", "a b"),
        ("user", "-x"),
        ("port", 0),
        ("port", 70000),
        ("keepalive_interval_s", -1),
    ],
)
def test_field_validation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, field: value})


@pytest.mark.parametrize(
    "addr", ["169.254.169.254", "169.254.0.1", "fe80::1", "100.100.100.200", "0.0.0.0", "::", "[fe80::1]"]
)
def test_unsafe_addresses_rejected(addr: str) -> None:
    with pytest.raises(ValueError):
        hosts.reject_unsafe_address(addr)
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, "host": addr})


@pytest.mark.parametrize("addr", ["2852039166", "0xa9fea9fe", "169.254.43518", "0251.0376.0251.0376", "0"])
def test_numeric_spellings_of_an_unsafe_address_are_rejected(addr: str) -> None:
    """The resolver reads these as 169.254.169.254 (or 0.0.0.0) without any DNS lookup."""
    with pytest.raises(ValueError):
        hosts.reject_unsafe_address(addr)
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, "host": addr})
    assert HostManifest(**{**GOOD, "host": "167772165"}).host == "167772165"  # 10.0.0.5: still allowed


@pytest.mark.parametrize(
    "addr",
    [
        "fd00:ec2::254",  # AWS IMDS over IPv6
        "[fd00:ec2::254]",
        "fd00:ec2::254%lo",
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "64:ff9b::169.254.169.254",
        "64:ff9b::6464:64c8",  # NAT64 of 100.100.100.200
        "2002:a9fe:a9fe::",  # 6to4 of 169.254.169.254
        "2002:a9fe:a9fe:1::5",
        "::a9fe:a9fe",  # IPv4-compatible (deprecated) spelling
    ],
)
def test_ipv6_spellings_of_a_metadata_address_are_rejected(addr: str) -> None:
    with pytest.raises(ValueError):
        hosts.reject_unsafe_address(addr)
    with pytest.raises(ValidationError):
        HostManifest(**{**GOOD, "host": addr})


@pytest.mark.parametrize(
    "addr",
    [
        "2001:db8::1",
        "2606:4700:4700::1111",  # an ordinary global IPv6 address
        "fd00:ec2::253",  # the same ULA prefix, another host
        "64:ff9b::a00:5",  # NAT64 of 10.0.0.5
        "2002:a00:5::1",  # 6to4 of 10.0.0.5
        "::1",
    ],
)
def test_ordinary_ipv6_addresses_stay_allowed(addr: str) -> None:
    hosts.reject_unsafe_address(addr)
    assert HostManifest(**{**GOOD, "host": addr}).host == addr


@pytest.mark.parametrize("addr", ["10.0.0.5", "127.0.0.1", "lab.example.com", "2001:db8::1", "::1"])
def test_ordinary_addresses_accepted(addr: str) -> None:
    hosts.reject_unsafe_address(addr)
    assert HostManifest(**{**GOOD, "host": addr}).host == addr


# ------------------------------------------------------------------ harness
def roots(home: Path, project: Path, *, trusted: bool, extra: Path | None = None) -> list[Root]:
    out = [Root("runtime", home), Root("project", project, trust_home=None if trusted else home)]
    return [*out, Root("extra:0", extra)] if extra else out


def test_runtime_root_host_loads(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write(home / "hosts" / "lab.yaml", host_yaml())
    h = Harness(roots(home, tmp_path / "p", trusted=True))
    assert set(h.hosts) == {"lab"} and h.hosts["lab"].manifest.user == "ubuntu"
    assert not h.errors


def test_untrusted_project_host_is_inert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_SECURITY_TRUST_ALL_WORKSPACES", raising=False)
    home, project = tmp_path / "home", tmp_path / "w" / ".auoreo"
    home.mkdir(exist_ok=True)
    path = write(project / "hosts" / "evil.yaml", host_yaml("evil", host="attacker.example"))
    h = Harness(roots(home, project, trusted=False))
    assert h.hosts == {} and ("hosts", "evil", path) in h.snapshot.untrusted


def test_trusted_project_and_extra_roots_never_define_hosts(tmp_path: Path) -> None:
    home, project, extra = tmp_path / "home", tmp_path / "w" / ".auoreo", tmp_path / "extra"
    home.mkdir(exist_ok=True)
    write(project / "hosts" / "evil.yaml", host_yaml("evil"))
    write(extra / "hosts" / "other.yaml", host_yaml("other"))
    h = Harness(roots(home, project, trusted=True, extra=extra))
    assert h.hosts == {}
    assert sum("runtime root only" in str(e) for e in h.errors) == 2


def test_runtime_host_wins_over_a_project_file_of_the_same_name(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "w" / ".auoreo"
    write(home / "hosts" / "lab.yaml", host_yaml())
    write(project / "hosts" / "lab.yaml", host_yaml(host="attacker.example"))
    h = Harness(roots(home, project, trusted=True))
    assert h.hosts["lab"].manifest.host == "10.0.0.5"


def test_invalid_host_file_is_a_load_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write(home / "hosts" / "lab.yaml", host_yaml(private_key="-----BEGIN"))
    write(home / "hosts" / "other.yaml", host_yaml("lab"))  # name must match the file
    h = Harness(roots(home, tmp_path / "p", trusted=True))
    assert h.hosts == {} and len(h.errors) == 2
    assert "BEGIN" not in "".join(map(str, h.errors))


def test_reload_reports_hosts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    h = Harness(roots(home, tmp_path / "p", trusted=True))
    write(home / "hosts" / "lab.yaml", host_yaml())
    assert h.reload()["hosts"] == {"added": ["lab"], "removed": []}


def test_host_file_changes_are_seen_without_a_reload(tmp_path: Path) -> None:
    """A separate process (`auoreo ssh add`, an editor, `rm`) changes `<home>/hosts/` under a
    running server: the next resolve sees it, and a revoked host fails closed at once."""
    from types import SimpleNamespace

    home = tmp_path / "home"
    hosts.write_host(home, "lab", {"host": "10.0.0.5", "user": "ubuntu", "key_secret": "secret:K"})
    h = Harness([Root("runtime", home)])
    st = Settings(home=home)
    st.m7.ssh.hosts = True
    svc = SimpleNamespace(settings=st, harness=h)
    assert hosts.resolve_environment(svc, "lab") is not None
    hosts.write_host(
        home, "lab", {"host": "10.0.0.5", "user": "deploy", "key_secret": "secret:K"}, force=True
    )
    env = hosts.resolve_environment(svc, "lab")
    assert env is not None and env.overrides["environment_ssh"].username == "deploy"
    hosts.write_host(home, "web", {"host": "10.0.0.6", "user": "ubuntu", "key_secret": "secret:K"})
    assert hosts.environment_names(svc) == ["lab", "web"]
    (home / "hosts" / "lab.yaml").unlink()  # the operator revokes the host in another shell
    assert hosts.resolve_environment(svc, "lab") is None
    assert hosts.environment_names(svc) == ["web"]


@pytest.mark.parametrize(
    "order",
    [
        ("auoreo.harness.manifests_hosts", "auoreo.harness.loader"),
        ("auoreo.harness.loader", "auoreo.harness.manifests_hosts"),
    ],
)
def test_loader_and_host_module_import_in_either_order(order: tuple[str, str]) -> None:
    code = (
        f"import {order[0]}, {order[1]}\nfrom auoreo.harness.loader import LOADERS as L\nassert 'hosts' in L"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    assert importlib.import_module(order[0])


# ------------------------------------------------------------------ files under <home>
def test_write_host_validates_and_refuses_overwrite(tmp_path: Path) -> None:
    fields = {k: v for k, v in GOOD.items() if k != "name"}
    path = hosts.write_host(tmp_path, "lab", fields)
    assert path == tmp_path / "hosts" / "lab.yaml"
    assert hosts.read_hosts(tmp_path)[0]["lab"].host == "10.0.0.5"
    with pytest.raises(FileExistsError):
        hosts.write_host(tmp_path, "lab", fields)
    hosts.write_host(tmp_path, "lab", {**fields, "port": 2222}, force=True)
    assert hosts.read_hosts(tmp_path)[0]["lab"].port == 2222
    for bad_name in ("con", "../up", "a/b"):
        with pytest.raises(ValueError):
            hosts.write_host(tmp_path, bad_name, fields)
    with pytest.raises(ValueError):
        hosts.write_host(tmp_path, "meta", {**fields, "host": "169.254.169.254"})
    with pytest.raises(ValueError):
        hosts.write_host(tmp_path, "leak", {**fields, "private_key": "x"})
    assert sorted(p.name for p in (tmp_path / "hosts").iterdir()) == ["lab.yaml"]


def test_override_points_at_the_harness_known_hosts(tmp_path: Path) -> None:
    over = hosts.manifest_to_override(HostManifest(**GOOD, port=2222, keepalive_interval_s=5), tmp_path)
    cfg = over["environment_ssh"]
    assert set(over) == {"environment_ssh"}
    assert (cfg.host, cfg.port, cfg.username) == ("10.0.0.5", 2222, "ubuntu")
    assert cfg.key_secret == "secret:SSH_KEY_LAB"
    assert cfg.known_hosts == tmp_path / "ssh" / "known_hosts" and cfg.keepalive_interval_s == 5


def test_trust_format_idempotency_and_replace(tmp_path: Path) -> None:
    kh = hosts.known_hosts_path(tmp_path)
    assert hosts.trust(tmp_path, "10.0.0.5", 22, KEY_A) is True
    assert hosts.trust(tmp_path, "10.0.0.5", 2222, KEY_A + " comment ignored") is True
    assert kh.read_text().splitlines() == [f"10.0.0.5 {KEY_A}", f"[10.0.0.5]:2222 {KEY_A}"]
    assert hosts.trust(tmp_path, "10.0.0.5", 22, KEY_A) is False  # same key: nothing rewritten
    assert kh.read_text().count("\n") == 2
    assert hosts.known_keys(tmp_path, "10.0.0.5", 22) == [KEY_A]
    assert hosts.known_keys(tmp_path, "10.0.0.5", 23) == []
    with pytest.raises(hosts.HostKeyChanged):  # a changed key is an error, never a silent update
        hosts.trust(tmp_path, "10.0.0.5", 22, KEY_B)
    assert hosts.known_keys(tmp_path, "10.0.0.5", 22) == [KEY_A]
    assert hosts.trust(tmp_path, "10.0.0.5", 22, KEY_B, replace=True) is True
    assert kh.read_text().splitlines() == [f"[10.0.0.5]:2222 {KEY_A}", f"10.0.0.5 {KEY_B}"]
    if sys.platform != "win32":
        assert kh.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "line", ["", "ssh-ed25519", "ssh-ed25519 AAAA\nevil ssh-rsa AAAA", "bad!type AAAA=", "ssh-rsa not*b64"]
)
def test_trust_rejects_malformed_key_lines(tmp_path: Path, line: str) -> None:
    with pytest.raises(ValueError):
        hosts.trust(tmp_path, "h", 22, line)
    assert not hosts.known_hosts_path(tmp_path).exists()


def test_trust_keeps_foreign_lines(tmp_path: Path) -> None:
    text = f"# note\n@revoked * {KEY_B}\nother,10.0.0.5 {KEY_A}\n"
    kh = write(hosts.known_hosts_path(tmp_path), text)
    assert hosts.known_keys(tmp_path, "10.0.0.5", 22) == [KEY_A]
    hosts.trust(tmp_path, "10.0.0.5", 22, KEY_B, replace=True)
    assert kh.read_text().splitlines() == [
        "# note",
        f"@revoked * {KEY_B}",
        f"other {KEY_A}",
        f"10.0.0.5 {KEY_B}",
    ]


def test_two_writers_do_not_lose_entries(tmp_path: Path) -> None:
    def writer(prefix: str) -> None:
        for i in range(25):
            hosts.trust(tmp_path, f"{prefix}{i}.example", 22, KEY_A)

    threads = [threading.Thread(target=writer, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = hosts.known_hosts_path(tmp_path).read_text().splitlines()
    assert len(lines) == 50 and len(set(lines)) == 50


def test_fingerprint_matches_openssh_form() -> None:
    asyncssh = pytest.importorskip("asyncssh")
    key = asyncssh.generate_private_key("ssh-ed25519")
    line = key.export_public_key().decode().strip()
    assert hosts.fingerprint_of(line) == key.get_fingerprint()
    assert hosts.fingerprint_of(line).startswith("SHA256:")


# ------------------------------------------------------------------ permissions and flag
def test_sensitive_write_globs_cover_hosts_and_known_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert {"$AUOREO_HOME/hosts/*", "$AUOREO_HOME/ssh/*"} <= set(SENSITIVE_WRITE_GLOBS)
    monkeypatch.setenv("AUOREO_HOME", str(tmp_path))
    engine = PermissionEngine([Rule(permission="edit", pattern="*", action="allow")], mode="yolo")
    for target in (tmp_path / "hosts" / "evil.yaml", tmp_path / "ssh" / "known_hosts"):
        assert engine.evaluate("edit", str(target)).action == "ask"
    assert engine.evaluate("edit", str(tmp_path / "notes.txt")).action == "allow"


def test_ssh_permission_rule_matches_a_host_name() -> None:
    engine = PermissionEngine([Rule(permission="ssh", pattern="lab", action="allow")])
    assert engine.evaluate("ssh", "lab", "ask").action == "allow"
    assert engine.evaluate("ssh", "other", "ask").action == "ask"


def test_flag_defaults_off_and_needs_no_restart() -> None:
    from auoreo.core import env_catalog

    assert Settings().m7.ssh.hosts is False
    assert not any("m7" in str(k) for k in env_catalog.RESTART_REQUIRED)


# ------------------------------------------------------------------ CLI: add, list
def test_cli_add_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_HOME", str(tmp_path))
    run = CliRunner().invoke
    base = ["--host", "10.0.0.5", "--user", "ubuntu", "--key", "SSH_KEY_LAB"]
    r = run(app, ["ssh", "add", "lab", *base])
    assert r.exit_code == 0 and "lab.yaml" in r.stdout
    assert hosts.read_hosts(tmp_path)[0]["lab"].key_secret == "secret:SSH_KEY_LAB"
    again = ["ssh", "add", "lab", "--host", "10.0.0.6", "--user", "ubuntu", "--key", "SSH_KEY_LAB"]
    assert run(app, again).exit_code == 2  # exists
    assert run(app, [*again, "--force"]).exit_code == 0
    assert hosts.read_hosts(tmp_path)[0]["lab"].host == "10.0.0.6"
    bad = [
        ["ssh", "add", "con", "--host", "h", "--user", "u", "--key", "K"],
        ["ssh", "add", "meta", "--host", "169.254.169.254", "--user", "u", "--key", "K"],
        ["ssh", "add", "raw", "--host", "h", "--user", "u", "--key", "-----BEGIN OPENSSH PRIVATE KEY-----"],
    ]
    for argv in bad:
        assert run(app, argv).exit_code == 2
    hosts.trust(tmp_path, "10.0.0.6", 22, KEY_A)
    hosts.write_host(tmp_path, "new", {"host": "h.example", "user": "u", "key_secret": "secret:K2"})
    r = run(app, ["ssh", "list", "--json"])
    rows = {row["name"]: row for row in json.loads(r.stdout)}
    want = {"name": "lab", "host": "10.0.0.6", "port": 22, "user": "ubuntu", "key": "SSH_KEY_LAB"}
    assert rows["lab"] == {**want, "trusted": True}
    assert rows["new"]["trusted"] is False
    text = run(app, ["ssh", "list"]).stdout
    assert "lab" in text and "10.0.0.6:22" in text and "SSH_KEY_LAB" in text
