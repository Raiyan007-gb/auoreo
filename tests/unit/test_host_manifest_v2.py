"""M7 hosts-b: host manifest v2 (`schema: 2`), the tolerant reader, jump chains, `delete_host`."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from auoreo.core import hosts
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.manifests_hosts import HOST_SCHEMA, HostManifest, parse_host
from auoreo.harness.registry import Harness

BASE = {"host": "10.0.0.5", "user": "ubuntu", "key_secret": "secret:SSH_KEY_LAB"}


def write(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_v1_file_round_trips_byte_identical_and_has_no_marker(tmp_path: Path) -> None:
    path = hosts.write_host(tmp_path, "lab", BASE)
    first = path.read_text()
    assert "schema" not in first and "tags" not in first and "jump" not in first
    m = hosts.read_hosts(tmp_path)[0]["lab"]
    hosts.write_host(tmp_path, "lab", m.model_dump(exclude={"name"}), force=True)
    assert path.read_text() == first
    # what the hosts-a build would see: exactly the six keys it knows
    v1 = {"name", "host", "port", "user", "key_secret", "keepalive_interval_s"}
    assert set(yaml.safe_load(first)) == v1


def test_writer_emits_only_set_v2_keys_with_the_marker(tmp_path: Path) -> None:
    path = hosts.write_host(tmp_path, "prod", {**BASE, "tags": ["readonly"], "description": "db"})
    data = yaml.safe_load(path.read_text())
    assert data["schema"] == HOST_SCHEMA == 2
    assert data["tags"] == ["readonly"] and data["description"] == "db"
    assert "jump" not in data and "audit_commands" not in data
    m = hosts.read_hosts(tmp_path)[0]["prod"]
    assert m.tags == ("readonly",) and m.version == 2


def test_newer_schema_is_rejected_whole(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="newer than this build"):
        parse_host({"name": "lab", **BASE, "schema": 3, "tags": ["readonly"]})
    write(tmp_path / "hosts" / "lab.json", {})  # ignored (not .yaml)
    write(tmp_path / "hosts" / "lab.yaml", {"name": "lab", **BASE, "schema": 3})
    found, problems = hosts.read_hosts(tmp_path)
    assert found == {} and "newer" in problems[0]
    h = Harness([Root("runtime", tmp_path)])
    assert h.hosts == {} and any("newer" in str(e) for e in h.errors)


def test_unknown_key_in_a_v2_file_is_dropped_with_a_warning(tmp_path: Path) -> None:
    m, warnings = parse_host({"name": "lab", **BASE, "schema": 2, "colour": "blue"})
    assert m.name == "lab" and warnings == ["unknown key 'colour' ignored"]
    write(tmp_path / "hosts" / "lab.yaml", {"name": "lab", **BASE, "schema": 2, "colour": "blue"})
    h = Harness([Root("runtime", tmp_path)])
    assert h.hosts["lab"].warnings == ("unknown key 'colour' ignored",)
    assert "colour" in hosts.read_hosts(tmp_path)[1][0]


def test_unknown_key_without_marker_stays_an_error() -> None:
    """A hosts-a file (no `schema`) keeps the strict reader: no tolerance by omission."""
    with pytest.raises(ValueError, match="colour"):
        parse_host({"name": "lab", **BASE, "colour": "blue"})
    with pytest.raises(ValueError):
        parse_host({"name": "lab", **BASE, "version": 2})


@pytest.mark.parametrize("bad", [True, "2", 0, -1, 2.0])
def test_marker_must_be_a_positive_int(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_host({"name": "lab", **BASE, "schema": bad})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tags", ["Read-Only"]),
        ("tags", [f"t{i}" for i in range(9)]),
        ("jump", "10.0.0.1"),
        ("jump", "user@bastion"),
        ("jump", "lab"),  # itself
        ("audit_commands", "full"),
        ("description", "x" * 201),
    ],
)
def test_v2_field_validation(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        parse_host({"name": "lab", **BASE, "schema": 2, field: value})


def test_v2_model_never_carries_key_material() -> None:
    for extra in ("private_key", "public_key", "fingerprint", "password", "shell", "policy", "forwards"):
        with pytest.raises(ValueError):
            HostManifest.model_validate({"name": "lab", **BASE, extra: "x"})


def svc_for(home: Path, *, jump: bool = True) -> Any:
    st = Settings(home=home)
    st.m7.ssh.hosts = True
    st.m7.ssh.jump = jump
    return SimpleNamespace(settings=st, harness=Harness([Root("runtime", home)]))


def test_jump_chain_resolves_one_hop(tmp_path: Path) -> None:
    hosts.write_host(tmp_path, "bastion", {**BASE, "host": "bastion.example", "key_secret": "secret:BK"})
    hosts.write_host(tmp_path, "db", {**BASE, "host": "10.1.0.9", "jump": "bastion"})
    env = hosts.resolve_environment(svc_for(tmp_path), "db")
    assert env is not None and env.host is not None and env.host.problem is None
    hop = env.host.jump
    assert hop is not None
    assert (hop.name, hop.host, hop.key_secret) == ("bastion", "bastion.example", "secret:BK")


@pytest.mark.parametrize(
    ("files", "why"),
    [
        ({"db": "nowhere"}, "not defined"),
        ({"db": "bastion", "bastion": "edge", "edge": None}, "one hop only"),
        ({"db": "bastion", "bastion": "db"}, "one hop only"),
    ],
)
def test_broken_chains_make_the_host_unusable(tmp_path: Path, files: dict[str, str | None], why: str) -> None:
    for name, jump in files.items():
        hosts.write_host(tmp_path, name, {**BASE, **({"jump": jump} if jump else {})})
    env = hosts.resolve_environment(svc_for(tmp_path), "db")
    assert env is not None and env.host is not None and why in (env.host.problem or "")


def test_jump_flag_off_refuses_rather_than_connecting_direct(tmp_path: Path) -> None:
    hosts.write_host(tmp_path, "bastion", BASE)
    hosts.write_host(tmp_path, "db", {**BASE, "jump": "bastion"})
    env = hosts.resolve_environment(svc_for(tmp_path, jump=False), "db")
    assert env is not None and env.host is not None
    assert "m7.ssh.jump is off" in (env.host.problem or "") and env.host.jump is None


def test_delete_host(tmp_path: Path) -> None:
    hosts.write_host(tmp_path, "lab", BASE)
    assert hosts.delete_host(tmp_path, "lab") is True
    assert hosts.delete_host(tmp_path, "lab") is False
    with pytest.raises(ValueError):
        hosts.delete_host(tmp_path, "../etc")
    with pytest.raises(ValueError):
        hosts.delete_host(tmp_path, "con")
