"""M7 hosts-b D5: `auoreo ssh keygen|pubkey|deploy|rotate|rm` and the operator-CLI floor.

The in-process server here reads `$HOME/.ssh/authorized_keys` of a temporary HOME on every
login (so a deployed key really grants access) and runs commands with that HOME."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auoreo.cli.commands import ssh_keys as sk
from auoreo.cli.commands.ssh import EXIT_CONNECT, EXIT_INVALID
from auoreo.core import floors, hosts
from auoreo.core import host_keys as hk
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from tests.unit.test_env_ssh import audit_rows

asyncssh = pytest.importorskip("asyncssh")
needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the test server runs commands with bash"
)
OLD = "SSH_KEY_OLD"


@dataclass
class KeyServer:
    port: int
    host_key: Any
    home: Path
    frozen: set[str] = field(default_factory=set)
    """Fingerprints refused even when listed (a server that ignores a new line)."""

    @property
    def authorized(self) -> Path:
        return self.home / ".ssh" / "authorized_keys"

    def lines(self) -> list[str]:
        return self.authorized.read_text().splitlines() if self.authorized.exists() else []


def _server_class(state: KeyServer) -> type:
    class Server(asyncssh.SSHServer):  # type: ignore[misc]
        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: Any) -> bool:
            want = key.export_public_key().decode().split()[:2]
            if key.get_fingerprint() in state.frozen:
                return False
            return any(line.split()[:2] == want for line in state.lines())

    return Server


def _handler(state: KeyServer) -> Any:
    async def handle(process: Any) -> None:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(state.home)}
        local = await asyncio.create_subprocess_exec(
            "bash", "-c", process.command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=env, cwd=state.home,
        )  # fmt: skip
        out, _ = await local.communicate()
        process.stdout.write(out)
        process.exit(local.returncode or 0)

    return handle


@pytest.fixture
async def key_server(tmp_path: Path) -> AsyncIterator[KeyServer]:
    home = tmp_path / "remote-home"
    home.mkdir()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    state = KeyServer(0, host_key, home)
    server = await asyncssh.create_server(
        _server_class(state), "127.0.0.1", 0, server_host_keys=[host_key],
        process_factory=_handler(state), encoding=None,
    )  # fmt: skip
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 2)


def setup(
    services: Services, srv: KeyServer, name: str = "lab", secret: str = OLD
) -> tuple[sk.Ops, list[str]]:
    """Host `name` reachable today with the stored key `secret` (authorized remotely)."""
    home = services.settings.home
    store = SecretStore(home)
    if not store.get(secret):
        old = asyncssh.generate_private_key("ssh-ed25519")
        store.set(secret, old.export_private_key().decode())
        srv.authorized.parent.mkdir(exist_ok=True)
        with srv.authorized.open("a") as fh:
            fh.write(old.export_public_key().decode())
    fields = {"host": "127.0.0.1", "port": srv.port, "user": "ops", "key_secret": f"secret:{secret}"}
    hosts.write_host(home, name, fields, force=True)
    hosts.trust(home, "127.0.0.1", srv.port, srv.host_key.export_public_key().decode().strip())
    out: list[str] = []
    return sk.Ops(services.settings, services.audit.record, out.append, services.harness), out


def private_texts(services: Services) -> list[str]:
    store = SecretStore(services.settings.home)
    return [v for n in store.names() if (v := store.get(n)) and "PRIVATE KEY" in v]


async def assert_no_private_text(services: Services, out: list[str]) -> None:
    keys = private_texts(services)
    bodies = [k.split("\n")[1] for k in keys]  # a base64 line of each private key
    files = [p.read_text() for p in (services.settings.home / "hosts").glob("*.yaml")]
    if hk.keys_path(services.settings.home).exists():
        files.append(hk.keys_path(services.settings.home).read_text())
    rows = []
    for action in (
        "host.key.generated",
        "host.key.deployed",
        "host.key.rotated",
        "host.key.revoked",
        "net.ssh",
    ):
        rows += [json.dumps(r.detail) + (r.reason or "") for r in await audit_rows(services, action)]
    blob = "\n".join([*out, *files, *rows])
    assert "PRIVATE KEY" not in blob and not any(b in blob for b in bodies)


# ------------------------------------------------------------------ pure helpers
def test_lines_and_commands_refuse_injection() -> None:
    kp = hk.generate()
    line = hk.authorized_line(kp, "SSH_KEY_LAB")
    assert hk.LINE_RE.match(line) and line.startswith(kp.public + " auoreo:SSH_KEY_LAB:")
    for bad in ("x;rm -rf ~", "a b", "$(id)", "", "x" * 65):
        with pytest.raises(ValueError):
            hk.authorized_line(kp, bad)
    for bad_line in (line + "\nssh-ed25519 AAAA evil", line + "; rm -rf ~", "ssh-rsa AAAA auoreo:X:abcdefgh"):
        with pytest.raises(ValueError):
            hk.install_command(bad_line)
    with pytest.raises(ValueError):
        hk.remove_command("ssh-ed25519 AAAA'; rm -rf ~; '")
    assert "'" + line + "'" in hk.install_command(line)
    assert "PRIVATE" not in repr(kp)


def test_from_private_never_echoes_the_value() -> None:
    with pytest.raises(ValueError) as info:
        hk.from_private("-----BEGIN NOT A KEY sekrit-material", secret="S")
    assert "sekrit" not in str(info.value) and "S" in str(info.value)


@pytest.mark.parametrize(
    "cmd",
    [
        "auoreo ssh add lab --host h --user u --key K",
        "auoreo ssh test lab --fingerprint SHA256:x",
        "auoreo ssh keygen lab",
        "auoreo ssh deploy lab --via OLD",
        "auoreo ssh rotate lab --revoke-old",
        "auoreo ssh import --write",
        "auoreo ssh rm lab",
        "uv run auoreo ssh rotate lab",
        "uvx auoreo ssh keygen lab",
        "python -m auoreo ssh deploy lab",
        "python3.13 -m auoreo ssh add x --host h --user u --key K",
        "/opt/venv/bin/auoreo ssh rm lab",
        "env A=1 auoreo ssh keygen lab",
        "sudo auoreo ssh rm lab",
        "echo hi && auoreo --home /tmp/h ssh rm lab",
        "bash -c 'auoreo ssh rotate lab'",
    ],
)
def test_floor_refuses_mutating_ssh_subcommands(cmd: str) -> None:
    why = floors.hardline(cmd)
    assert why is not None and "operator command" in why


@pytest.mark.parametrize(
    "cmd",
    [
        "auoreo ssh list",
        "auoreo ssh list --json",
        "auoreo ssh pubkey lab",
        "auoreo doctor",
        "ssh lab test",
        "echo auoreo ssh",
    ],
)
def test_floor_leaves_read_only_commands(cmd: str) -> None:
    assert floors.hardline(cmd) is None


# ------------------------------------------------------------------ keygen / pubkey
async def test_keygen_stores_the_key_only_in_the_store(services: Services, tmp_path: Path) -> None:
    hosts.write_host(
        services.settings.home, "lab", {"host": "10.0.0.5", "user": "u", "key_secret": "secret:K"}
    )
    out: list[str] = []
    ops = sk.Ops(services.settings, services.audit.record, out.append, services.harness)
    assert await sk.keygen(ops, "lab") == 0
    store = SecretStore(services.settings.home)
    priv = store.get("SSH_KEY_LAB")
    assert priv and "PRIVATE KEY" in priv
    assert hosts.read_hosts(services.settings.home)[0]["lab"].key_secret == "secret:SSH_KEY_LAB"
    kp = hk.from_private(priv, secret="SSH_KEY_LAB")
    assert out[0] == hk.authorized_line(kp, "SSH_KEY_LAB") and kp.fingerprint in out[1]
    assert hk.read_keys(services.settings.home)["SSH_KEY_LAB"]["fingerprint"] == kp.fingerprint
    row = (await audit_rows(services, "host.key.generated"))[0]
    assert row.actor == "operator" and row.detail["fingerprint"] == kp.fingerprint
    assert await sk.keygen(ops, "lab") == EXIT_INVALID  # refuses an existing secret
    assert store.get("SSH_KEY_LAB") == priv
    assert await sk.keygen(ops, "lab", "bad name;x") == EXIT_INVALID
    await assert_no_private_text(services, out)


async def test_pubkey_derives_from_the_secret_not_keys_json(services: Services) -> None:
    hosts.write_host(
        services.settings.home, "lab", {"host": "10.0.0.5", "user": "u", "key_secret": "secret:K"}
    )
    out: list[str] = []
    ops = sk.Ops(services.settings, services.audit.record, out.append, services.harness)
    await sk.keygen(ops, "lab")
    path = hk.keys_path(services.settings.home)
    planted = json.loads(path.read_text())
    planted["SSH_KEY_LAB"]["fingerprint"] = "SHA256:attacker"
    path.write_text(json.dumps(planted))
    out.clear()
    assert await sk.pubkey(ops, "lab") == 0
    assert "attacker" not in "".join(out) and out[0].startswith("ssh-ed25519 ")


# ------------------------------------------------------------------ deploy / rotate
@needs_bash
async def test_keygen_then_deploy_via_old_key_is_idempotent_and_verified(
    services: Services, key_server: KeyServer
) -> None:
    ops, out = setup(services, key_server)
    assert await sk.keygen(ops, "lab") == 0
    assert await sk.deploy(ops, "lab", via=OLD) == 0, out
    new = hk.from_private(SecretStore(services.settings.home).get("SSH_KEY_LAB"), secret="SSH_KEY_LAB")
    mine = [ln for ln in key_server.lines() if ln.startswith(new.public)]
    assert len(mine) == 1 and mine[0] == hk.authorized_line(new, "SSH_KEY_LAB")
    assert await sk.deploy(ops, "lab") == 0  # now with the host's own key; no duplicate line
    assert len([ln for ln in key_server.lines() if ln.startswith(new.public)]) == 1
    rows = await audit_rows(services, "host.key.deployed")
    assert [r.detail["verified"] for r in rows] == [True, True] and rows[0].detail["via"] == OLD
    assert hk.read_keys(services.settings.home)["SSH_KEY_LAB"]["deployed"] == ["lab"]
    assert {r.actor for r in await audit_rows(services, "net.ssh")} == {"operator"}
    await assert_no_private_text(services, out)


@needs_bash
async def test_deploy_fails_closed_without_a_working_login(services: Services, key_server: KeyServer) -> None:
    ops, out = setup(services, key_server)
    await sk.keygen(ops, "lab")
    assert await sk.deploy(ops, "lab") == EXIT_CONNECT  # the new key is not authorized yet
    assert await sk.deploy(ops, "lab", via="NOPE") == EXIT_INVALID
    row = (await audit_rows(services, "host.key.deployed"))[0]
    assert row.decision == "deny" and row.detail["verified"] is False


@needs_bash
async def test_rotate_proves_the_new_key_before_swapping(services: Services, key_server: KeyServer) -> None:
    ops, out = setup(services, key_server)
    store = SecretStore(services.settings.home)
    old = store.get(OLD)
    real_generate = hk.generate
    fresh = real_generate()
    key_server.frozen.add(fresh.fingerprint)  # the server ignores the new line: login fails
    sk.hk.generate = lambda: fresh  # type: ignore[attr-defined]
    try:
        assert await sk.rotate(ops, "lab") == EXIT_CONNECT
    finally:
        sk.hk.generate = real_generate  # type: ignore[attr-defined]
    assert store.get(OLD) == old and store.candidates(OLD) == [old]  # nothing changed locally
    assert (await audit_rows(services, "host.key.rotated"))[0].decision == "deny"
    key_server.frozen.clear()
    assert await sk.rotate(ops, "lab", grace_h=1) == 0, out
    new = store.get(OLD)
    assert new != old and store.candidates(OLD) == [new, old]  # old kept as #prev in the grace
    # the pool's backend logs in with the new key; #prev still works inside the window
    ok, text = await ops.run(hosts.read_hosts(services.settings.home)[0]["lab"], [new or ""], hk.PROBE)
    assert ok and "auoreo-ssh-ok" in text
    row = (await audit_rows(services, "host.key.rotated"))[-1]
    assert row.decision == "allow" and row.detail["grace_s"] == 3600 and row.detail["hosts"] == ["lab"]
    await assert_no_private_text(services, out)


@needs_bash
async def test_rotate_shared_secret_skip_and_revoke(
    services: Services, key_server: KeyServer, tmp_path: Path
) -> None:
    ops, out = setup(services, key_server)
    # a second host with the same secret that is down
    hosts.write_host(
        services.settings.home,
        "down",
        {"host": "127.0.0.1", "port": 1, "user": "ops", "key_secret": f"secret:{OLD}"},
    )
    store = SecretStore(services.settings.home)
    old = store.get(OLD)
    assert await sk.rotate(ops, "lab") == EXIT_CONNECT and store.get(OLD) == old
    assert await sk.rotate(ops, "lab", skip=["down"], revoke_old=True) == 0, out
    new = store.get(OLD)
    assert new != old and store.candidates(OLD) == [new, old]  # skipped host keeps #prev
    old_pub = hk.from_private(old, secret=OLD).public
    assert not any(ln.startswith(old_pub) for ln in key_server.lines())  # revoked on lab
    lab = hosts.read_hosts(services.settings.home)[0]["lab"]
    assert not (await ops.run(lab, [old or ""], hk.PROBE))[0]  # the old key no longer logs in
    assert (await audit_rows(services, "host.key.rotated"))[-1].detail["skipped"] == ["down"]
    assert (await audit_rows(services, "host.key.revoked"))[-1].decision == "allow"


async def test_rm_keeps_the_secret_and_can_forget_the_host_key(services: Services) -> None:
    home = services.settings.home
    hosts.write_host(home, "lab", {"host": "10.0.0.5", "user": "u", "key_secret": "secret:K"})
    SecretStore(home).set("K", "value")
    hosts.trust(
        home,
        "10.0.0.5",
        22,
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINsODwY14lVJHbIWemgeTyJYQTn4CwG5ST0SZm8pBl78",
    )
    hosts.trust(
        home,
        "10.0.0.6",
        22,
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake",
    )
    out: list[str] = []
    ops = sk.Ops(services.settings, services.audit.record, out.append, services.harness)
    assert await sk.remove(ops, "lab", forget_key=True) == 0
    assert "lab" not in hosts.read_hosts(home)[0] and SecretStore(home).get("K") == "value"
    assert not hosts.known_keys(home, "10.0.0.5", 22) and hosts.known_keys(home, "10.0.0.6", 22)
    assert await sk.remove(ops, "lab") == EXIT_INVALID
    row = (await audit_rows(services, "host.deleted"))[0]
    assert row.actor == "operator" and row.detail["changed"] == ["file", "known_hosts"]
