"""M7 hosts-c D6: one-hop jump hosts (`jump: <host>` in a host file, `m7.ssh.jump`).

A second in-process server acts as the bastion: it authenticates its own user key, records
every key offered to it, and forwards `direct-tcpip` channels to the target server."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auoreo.cli.commands import ssh as ssh_cli
from auoreo.core import hosts
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from auoreo.tools.environments import pool_for
from tests.unit import test_env_ssh
from tests.unit.test_env_ssh import BASE, KEY_NAME, WHY, SshServer, audit_rows, session, tool

asyncssh = pytest.importorskip("asyncssh")
ssh_server = test_env_ssh.ssh_server
pytestmark = test_env_ssh.pytestmark
JUMP_KEY = "SSH_KEY_BASTION"


@dataclass
class Bastion:
    port: int
    host_key: Any
    user_key: Any
    offered: list[str] = field(default_factory=list)
    """Fingerprints of every client key offered to the bastion."""
    forwarded: list[tuple[str, int]] = field(default_factory=list)


@pytest.fixture
async def bastion() -> AsyncIterator[Bastion]:
    state = Bastion(
        0, asyncssh.generate_private_key("ssh-ed25519"), asyncssh.generate_private_key("ssh-ed25519")
    )
    allowed = state.user_key.export_public_key().decode().split()[:2]

    class Server(asyncssh.SSHServer):  # type: ignore[misc]
        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: Any) -> bool:
            state.offered.append(key.get_fingerprint())
            return key.export_public_key().decode().split()[:2] == allowed

        def connection_requested(
            self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
        ) -> bool:
            state.forwarded.append((dest_host, dest_port))
            return True  # standard forwarding to the target

    server = await asyncssh.create_server(Server, "127.0.0.1", 0, server_host_keys=[state.host_key])
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 2)


def chain(services: Services, srv: SshServer, b: Bastion, *, trust_target: bool = True, **db: Any) -> None:
    home = services.settings.home
    services.settings.m7.ssh.hosts = True
    services.settings.m7.ssh.jump = True
    store = SecretStore(home)
    store.set(KEY_NAME, srv.user_key.export_private_key().decode())
    store.set(JUMP_KEY, b.user_key.export_private_key().decode())
    jump = {"host": "127.0.0.1", "port": b.port, "user": "jump", "key_secret": f"secret:{JUMP_KEY}"}
    hosts.write_host(home, "bastion", jump, force=True)
    hosts.write_host(
        home, "db",
        {"host": "127.0.0.1", "port": srv.port, "user": "agent", "key_secret": f"secret:{KEY_NAME}",
         "jump": "bastion", **db},
        force=True,
    )  # fmt: skip
    hosts.trust(home, "127.0.0.1", b.port, b.host_key.export_public_key().decode().strip())
    if trust_target:
        hosts.trust(home, "127.0.0.1", srv.port, srv.host_key.export_public_key().decode().strip())
    services.harness.reload()


async def wait_card(services: Services) -> Any:
    for _ in range(300):
        if pending := services.approvals.pending():
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no approval was requested")


async def test_cuj3_one_card_two_rows_and_the_target_key_never_reaches_the_bastion(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path
) -> None:
    chain(services, ssh_server, bastion)
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine, interactive=True)
    call = asyncio.create_task(
        tool(services, ctx, engine, "use_environment", {"name": "db", "justification": WHY})
    )
    card = await wait_card(services)
    assert card.pattern == "db" and "via" in card.detail and "(bastion)" in card.detail and card.local_only
    await services.approvals.reply(card.request_id, "once")
    text, err, _ = await call
    assert not err, text
    assert len(services.approvals.pending()) == 0
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo via-$((6*7))"})
    assert not err and "via-42" in text
    grant = (await audit_rows(services, "net.ssh_grant"))[0].detail
    assert grant["chain"] == ["bastion", "db"]
    opens = [r.detail for r in await audit_rows(services, "net.ssh")]
    assert [(o["name"], o["role"]) for o in opens] == [("bastion", "jump"), ("db", "target")]
    assert {o["grant_id"] for o in opens} == {grant["grant_id"]} and opens[1]["via"] == "bastion"
    assert bastion.forwarded == [("127.0.0.1", ssh_server.port)]
    assert ssh_server.user_key.get_fingerprint() not in bastion.offered  # end to end only
    assert set(bastion.offered) == {bastion.user_key.get_fingerprint()}
    await pool_for(services).close()


@pytest.mark.parametrize("denied", ["bastion", "db"])
async def test_a_deny_on_either_hop_refuses(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path, denied: str
) -> None:
    chain(services, ssh_server, bastion)
    engine = PermissionEngine(
        [
            *BASE,
            Rule(permission="ssh", pattern="*", action="allow"),
            Rule(permission="ssh", pattern=denied, action="deny"),
        ]
    )
    ctx = session(services, workdir, engine, interactive=True)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "db", "justification": WHY})
    assert err and "denied" in text and not ctx.state.ssh_granted
    assert not bastion.offered and not await audit_rows(services, "net.ssh")


async def test_a_rule_on_the_target_alone_does_not_cover_the_jump(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path
) -> None:
    chain(services, ssh_server, bastion)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="db", action="allow")])
    ctx = session(services, workdir, engine)  # non-interactive: needs a rule for both hops
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "db"})
    assert err and "`ssh bastion`" in text and "`ssh db`" in text and not bastion.offered
    ctx = session(services, workdir, engine, sid="s2", interactive=True)
    call = asyncio.create_task(
        tool(services, ctx, engine, "use_environment", {"name": "db", "justification": WHY})
    )
    card = await wait_card(services)  # interactive: a human still decides for the chain
    await services.approvals.reply(card.request_id, "reject")
    text, err, _ = await call
    assert err and "rejected" in text and not bastion.offered
    engine = PermissionEngine(
        [
            *BASE,
            Rule(permission="ssh", pattern="db", action="allow"),
            Rule(permission="ssh", pattern="bastion", action="allow"),
        ]
    )
    ctx = session(services, workdir, engine, sid="s3")
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "db"})
    assert not err, text
    await pool_for(services).close()


async def test_changed_target_key_fails_closed_through_the_tunnel(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path
) -> None:
    chain(services, ssh_server, bastion, trust_target=False)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    hosts.trust(services.settings.home, "127.0.0.1", ssh_server.port, other)
    rules = [
        *BASE,
        Rule(permission="ssh", pattern="db", action="allow"),
        Rule(permission="ssh", pattern="bastion", action="allow"),
    ]
    engine = PermissionEngine(rules)
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "db"})
    assert err and "host key does not match" in text and not ssh_server.locals
    await pool_for(services).close()


async def test_jump_flag_off_never_connects_direct(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path
) -> None:
    chain(services, ssh_server, bastion)
    services.settings.m7.ssh.jump = False
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="*", action="allow")])
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "db"})
    assert err and "m7.ssh.jump is off" in text
    assert not bastion.offered and not ssh_server.locals and not await audit_rows(services, "net.ssh")


async def test_editing_the_jump_host_asks_again(
    services: Services, ssh_server: SshServer, bastion: Bastion, workdir: Path
) -> None:
    chain(services, ssh_server, bastion)
    rules = [
        *BASE,
        Rule(permission="ssh", pattern="db", action="allow"),
        Rule(permission="ssh", pattern="bastion", action="allow"),
    ]
    engine = PermissionEngine(rules)
    ctx = session(services, workdir, engine)
    await tool(services, ctx, engine, "use_environment", {"name": "db"})
    first = set(ctx.state.ssh_granted)
    hosts.write_host(
        services.settings.home, "bastion",
        {"host": "127.0.0.1", "port": bastion.port, "user": "other", "key_secret": f"secret:{JUMP_KEY}"},
        force=True,
    )  # fmt: skip
    services.harness.reload()
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo again"})
    assert len(await audit_rows(services, "net.ssh_grant")) == 2 and set(ctx.state.ssh_granted) != first
    await pool_for(services).close()


async def test_cli_test_probes_the_target_through_the_bastion(
    services: Services, ssh_server: SshServer, bastion: Bastion
) -> None:
    chain(services, ssh_server, bastion, trust_target=False)
    out: list[str] = []
    fp = ssh_server.host_key.get_fingerprint()
    code = await ssh_cli.run_test(
        services.settings, "db", record=services.audit.record, harness=services.harness,
        fingerprint=fp, echo=out.append, timeout=5,
    )  # fmt: skip
    assert code == 0, out
    assert hosts.known_keys(services.settings.home, "127.0.0.1", ssh_server.port)
    assert ("127.0.0.1", ssh_server.port) in bastion.forwarded
    # an untrusted bastion stops the probe: nothing is sent through an unverified hop
    hosts.forget_host_key(services.settings.home, "127.0.0.1", bastion.port)
    out.clear()
    code = await ssh_cli.run_test(
        services.settings, "db", record=services.audit.record, harness=services.harness,
        fingerprint=fp, echo=out.append, timeout=5,
    )  # fmt: skip
    assert code == ssh_cli.EXIT_CONNECT and "trust it first" in out[-1]
