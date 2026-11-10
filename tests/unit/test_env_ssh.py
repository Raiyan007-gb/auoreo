"""ssh environment backend against an in-process asyncssh server.

The server authenticates one generated user key, presents a generated host key and runs
each channel's command with a real local `bash -c` (stdin/stdout piped through), so the
backend's exec, stdin, yield/poll, PTY request, SFTP and fail-closed paths run for real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.backends.environment import ExecRequest
from auoreo.cli.commands import ssh as ssh_cli
from auoreo.core import hosts, justifications
from auoreo.core.approvals import reply_with_grants
from auoreo.core.db import AuditRow, EventRow
from auoreo.core.diagnostics import run_checks
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.secrets import SecretStore, UnscopedSecretError, secret_scope
from auoreo.core.services import Services
from auoreo.core.settings import NamedEnvironment, SshEnvironment
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import SessionState, ToolContext, restore_state
from auoreo.tools.environments import apply_overrides, pool_for
from auoreo.tools.gates import secret_names
from auoreo.tools.shell_sessions import ShellError

asyncssh = pytest.importorskip("asyncssh")
pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the test server runs commands with bash"
)

KEY_NAME = "SSH_TEST_KEY"


@dataclass
class SshServer:
    port: int
    host_key: Any
    user_key: Any
    locals: list[asyncio.subprocess.Process]

    def known_hosts(self, path: Path, key: Any | None = None) -> Path:
        pub = (key or self.host_key).export_public_key().decode().strip()
        path.write_text(f"[127.0.0.1]:{self.port} {pub}\n")
        return path


class _Server(asyncssh.SSHServer):  # type: ignore[misc]
    def begin_auth(self, username: str) -> bool:
        return True  # always require auth (publickey against authorized_client_keys)


def _handler(spawned: list[asyncio.subprocess.Process]) -> Any:
    async def handle(process: Any) -> None:
        env = {"PATH": "/usr/bin:/bin", "TERM": process.get_terminal_type() or "none"}
        local = await asyncio.create_subprocess_exec(
            "bash", "-c", process.command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
            start_new_session=True,
        )  # fmt: skip
        spawned.append(local)

        async def feed() -> None:
            assert local.stdin is not None
            with contextlib.suppress(Exception):
                while data := await process.stdin.read(65536):
                    local.stdin.write(data)
                    await local.stdin.drain()
            with contextlib.suppress(Exception):
                local.stdin.close()

        task = asyncio.create_task(feed())
        assert local.stdout is not None
        with contextlib.suppress(Exception):
            while data := await local.stdout.read(65536):
                process.stdout.write(data)
        code = await local.wait()
        task.cancel()
        process.exit(code)

    return handle


@pytest.fixture
async def ssh_server() -> AsyncIterator[SshServer]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    user_key = asyncssh.generate_private_key("ssh-ed25519")
    auth = asyncssh.import_authorized_keys(user_key.export_public_key().decode())
    spawned: list[asyncio.subprocess.Process] = []
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[host_key], authorized_client_keys=auth,
        process_factory=_handler(spawned), encoding=None, sftp_factory=True,
    )  # fmt: skip
    port = server.sockets[0].getsockname()[1]
    try:
        yield SshServer(port, host_key, user_key, spawned)
    finally:
        for p in spawned:
            if p.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    p.kill()
        server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 2)


def configure(services: Services, srv: SshServer, tmp_path: Path, **over: Any) -> None:
    SecretStore(services.settings.home).set(KEY_NAME, srv.user_key.export_private_key().decode())
    cfg = {
        "host": "127.0.0.1",
        "port": srv.port,
        "username": "agent",
        "key_secret": f"secret:{KEY_NAME}",
        "known_hosts": srv.known_hosts(tmp_path / "known_hosts"),
        "connect_timeout_s": 5,
        **over,
    }
    services.settings.environment_ssh = SshEnvironment(**cfg)


def backend(services: Services) -> Any:
    bdef = services.harness.select_backend("environment", "ssh")
    assert bdef is not None
    return bdef.load()(services.settings)


async def test_exec_exit_code_and_merged_stderr(
    services: Services, ssh_server: SshServer, tmp_path: Path
) -> None:
    configure(services, ssh_server, tmp_path)
    b = backend(services)
    assert b.remote is True and (await b.check()).ok
    chunk = await b.exec_command(ExecRequest("echo out; echo err >&2; exit 3"), session_id="s1")
    assert chunk.exit_code == 3 and "out" in chunk.output and "err" in chunk.output
    chunk = await b.exec_command(ExecRequest("pwd", workdir=str(tmp_path)), session_id="s1")
    assert chunk.exit_code == 0 and str(tmp_path) in chunk.output
    await b.close()


async def test_interactive_stdin_and_yield(services: Services, ssh_server: SshServer, tmp_path: Path) -> None:
    configure(services, ssh_server, tmp_path)
    b = backend(services)
    chunk = await b.exec_command(ExecRequest("read x; echo got:$x", yield_time_ms=200), session_id="s1")
    assert chunk.exit_code is None and chunk.process_id
    chunk = await b.write_stdin(chunk.process_id, "abc\n", yield_time_ms=5000)
    assert chunk.exit_code == 0 and "got:abc" in chunk.output
    # yield then poll
    chunk = await b.exec_command(ExecRequest("sleep 0.4; echo done", yield_time_ms=50), session_id="s1")
    assert chunk.exit_code is None
    pid = chunk.process_id
    assert pid is not None
    chunk = await b.write_stdin(pid, "", yield_time_ms=5000)
    assert chunk.exit_code == 0 and "done" in chunk.output
    with pytest.raises(ShellError, match="no running"):
        await b.write_stdin(pid, "", yield_time_ms=10)
    # a long process is killed at session close
    chunk = await b.exec_command(ExecRequest("sleep 30", yield_time_ms=50), session_id="s2")
    assert chunk.exit_code is None
    await b.close_session("s2")
    assert not b.table.live("s2")
    await b.close()


async def test_tty_requests_a_terminal_type(
    services: Services, ssh_server: SshServer, tmp_path: Path
) -> None:
    configure(services, ssh_server, tmp_path)
    b = backend(services)
    chunk = await b.exec_command(ExecRequest("echo term=$TERM", tty=True), session_id="s1")
    assert chunk.exit_code == 0 and "term=dumb" in chunk.output
    await b.close()


async def test_sftp_upload_download(services: Services, ssh_server: SshServer, tmp_path: Path) -> None:
    configure(services, ssh_server, tmp_path)
    b = backend(services)
    src = tmp_path / "a.txt"
    src.write_text("payload")
    await b.upload(str(src), str(tmp_path / "remote.txt"))
    await b.download(str(tmp_path / "remote.txt"), str(tmp_path / "back.txt"))
    assert (tmp_path / "back.txt").read_text() == "payload"
    await b.close()


async def test_host_key_mismatch_fails_closed(
    services: Services, ssh_server: SshServer, tmp_path: Path
) -> None:
    configure(services, ssh_server, tmp_path)
    other = asyncssh.generate_private_key("ssh-ed25519")
    ssh_server.known_hosts(tmp_path / "known_hosts", other)
    b = backend(services)
    with pytest.raises(ShellError, match="host key does not match"):
        await b.exec_command(ExecRequest("echo never"), session_id="s1")
    status = await b.check()
    assert not status.ok and "fail closed" in status.detail
    assert not ssh_server.locals  # nothing ran


async def test_wrong_key_refused(services: Services, ssh_server: SshServer, tmp_path: Path) -> None:
    configure(services, ssh_server, tmp_path)
    wrong = asyncssh.generate_private_key("ssh-ed25519")
    SecretStore(services.settings.home).set(KEY_NAME, wrong.export_private_key().decode())
    with pytest.raises(ShellError, match="authentication failed"):
        await backend(services).exec_command(ExecRequest("echo never"), session_id="s1")
    assert not ssh_server.locals


async def test_known_hosts_and_secret_key_required(
    services: Services, ssh_server: SshServer, tmp_path: Path
) -> None:
    configure(services, ssh_server, tmp_path, known_hosts=None)
    b = backend(services)
    status = await b.check()
    assert not status.ok and "known_hosts is required" in status.detail
    with pytest.raises(ShellError, match="known_hosts is required"):
        await b.exec_command(ExecRequest("echo never"), session_id="s1")
    configure(services, ssh_server, tmp_path, key_secret=None)
    status = await backend(services).check()
    assert not status.ok and "key_secret is required" in status.detail
    with pytest.raises(ValueError):
        SshEnvironment(host="h", username="u", key_secret="/home/u/.ssh/id_ed25519")  # files never


async def test_connect_timeout_fails_closed(services: Services, tmp_path: Path) -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)  # accepts TCP, never speaks SSH

    srv = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    key = asyncssh.generate_private_key("ssh-ed25519")
    SecretStore(services.settings.home).set(KEY_NAME, key.export_private_key().decode())
    kh = tmp_path / "kh"
    kh.write_text(f"[127.0.0.1]:{port} {key.export_public_key().decode().strip()}\n")
    services.settings.environment_ssh = SshEnvironment(
        host="127.0.0.1", port=port, username="u", key_secret=f"secret:{KEY_NAME}", known_hosts=kh,
        connect_timeout_s=0.3,
    )  # fmt: skip
    with pytest.raises(ShellError, match="timed out"):
        await backend(services).exec_command(ExecRequest("echo never"), session_id="s1")
    srv.close()


async def test_tool_path_routes_audits_and_keeps_floors(
    services: Services, ssh_server: SshServer, tmp_path: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(services, ssh_server, tmp_path)
    services.settings.environments = {"box": NamedEnvironment(backend="ssh")}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    engine = PermissionEngine([Rule(permission="bash", pattern="*", action="allow")])
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    ctx.state.environment = "box"

    async def call(name: str, args: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
        out = await services.coordinator.runner.executor.run(
            ToolCall("c", name, json.dumps(args)), tools=services.harness.tools, ctx=ctx, engine=engine
        )
        return out.result.content, out.result.is_error, out.result.meta

    text, err, meta = await call("exec_command", {"cmd": "echo key=${OPENROUTER_API_KEY:-unset}"})
    assert not err and "key=unset" in text and meta["environment"] == "box" and meta["remote"] is True
    text, err, meta = await call("exec_command", {"cmd": "cat", "yield_time_ms": 200})
    assert not err and meta.get("running")
    text, err, _ = await call("write_stdin", {"session_id": meta["session_id"], "chars": "hello\n"})
    assert not err and "hello" in text
    text, err, _ = await call("write_stdin", {"session_id": meta["session_id"], "chars": "rm -rf /\n"})
    assert err and "floor" in text  # floors apply to every line sent to the remote process
    text, err, _ = await call("exec_command", {"cmd": "rm -rf /"})
    assert err and "denied" in text  # executor floor: nothing reaches the backend
    async with services.db.session() as s:
        rows = list(
            (await s.execute(select(AuditRow).where(AuditRow.action == "environment.exec"))).scalars()
        )
    assert len(rows) >= 3
    assert rows[0].detail["backend"] == "ssh" and rows[0].detail["remote"] is True
    assert len(rows[0].detail["cmd_hash"]) == 16 and "OPENROUTER" not in json.dumps(rows[0].detail)
    assert not any("rm -rf" in json.dumps(r.detail) for r in rows)
    pool = pool_for(services)
    rec = next(iter(pool.procs["s1"].values()))
    await services.emit("s1", "session.archived", {})  # the pool's bus watch closes the session
    for _ in range(100):
        if not rec.backend.table.live("s1"):
            break
        await asyncio.sleep(0.02)
    assert not rec.backend.table.live("s1") and "s1" not in pool.procs
    await pool.close()  # drops the ssh connection before the server fixture stops


# =========================================================================== M7 hosts-a
# `hosts/<name>.yaml`, the per-host `ssh` gate, `net.ssh*` audit and CLI trust-on-first-use.


def flag(services: Services, on: bool = True) -> None:
    services.settings.m7.ssh.hosts = on


def host_file(services: Services, srv: SshServer, name: str = "lab", trust: bool = True, **over: Any) -> None:
    """A runtime-root host file for the test server, its key in the store, its host key trusted."""
    home = services.settings.home
    SecretStore(home).set(KEY_NAME, srv.user_key.export_private_key().decode())
    fields = {"host": "127.0.0.1", "port": srv.port, "user": "agent", "key_secret": f"secret:{KEY_NAME}"}
    hosts.write_host(home, name, {**fields, **over}, force=True)
    if trust:
        hosts.trust(home, "127.0.0.1", srv.port, srv.host_key.export_public_key().decode().strip())
    services.harness.reload()


BASE = [
    Rule(permission="bash", pattern="*", action="allow"),
    Rule(permission="environment", pattern="*", action="allow"),
]
WHY = "run the nightly log check on the lab machine"


def session(
    services: Services, workdir: Path, engine: PermissionEngine, sid: str = "s1", **kw: Any
) -> ToolContext:
    return ToolContext(
        session_id=sid, cwd=workdir, home=services.settings.home, services={"engine": engine}, **kw
    )


async def tool(
    services: Services, ctx: ToolContext, engine: PermissionEngine, name: str, args: dict[str, Any]
) -> tuple[str, bool, dict[str, Any]]:
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", name, json.dumps(args)), tools=services.harness.tools, ctx=ctx, engine=engine
    )
    return out.result.content, out.result.is_error, out.result.meta


async def audit_rows(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        stmt = select(AuditRow).where(AuditRow.action == action).order_by(AuditRow.seq)
        return list((await s.execute(stmt)).scalars())


async def answer(services: Services, engine: PermissionEngine, reply: str) -> Any:
    """Reply to the one pending approval the way REST/WS do; returns the request."""
    for _ in range(300):
        if pending := services.approvals.pending():
            assert len(pending) == 1
            await reply_with_grants(services.approvals, lambda _sid: engine, pending[0].request_id, reply)  # type: ignore[arg-type]
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no approval was requested")


async def test_host_file_runs_through_use_environment_with_grant_and_socket_audit(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server, keepalive_interval_s=7)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {})
    assert not err and "- lab: backend ssh" in text
    text, err, meta = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert not err and meta == {"environment": "lab", "backend": "ssh", "remote": True}, text
    text, err, meta = await tool(services, ctx, engine, "exec_command", {"cmd": "echo hi-$((40+2))"})
    assert not err and "hi-42" in text and meta["environment"] == "lab"
    grants = await audit_rows(services, "net.ssh_grant")
    assert len(grants) == 1 and grants[0].actor == "agent" and grants[0].session_id == "s1"
    grant_id = grants[0].detail["grant_id"]
    want = {"mode": "ssh", "name": "lab", "host": "127.0.0.1", "port": ssh_server.port}
    assert want.items() <= grants[0].detail.items() and grants[0].detail["justificationHash"] is None
    opens = await audit_rows(services, "net.ssh")
    assert [r.detail for r in opens] == [{**want, "grant_id": grant_id}]
    assert opens[0].actor == "agent" and opens[0].session_id == "s1"
    # a dropped connection is reopened silently; the new socket is audited under the same grant
    backend = pool_for(services).instances["lab"]
    backend._conn.close()
    await backend._conn.wait_closed()
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo again"})
    assert not err and "again" in text
    opens = await audit_rows(services, "net.ssh")
    assert len(opens) == 2 and opens[1].detail["grant_id"] == grant_id
    assert len(await audit_rows(services, "net.ssh_grant")) == 1  # one grant per session and host
    assert backend.cfg.keepalive_interval_s == 7 and backend.cfg.known_hosts == hosts.known_hosts_path(
        services.settings.home
    )
    checks = {c.check: c for c in await run_checks(services.settings, services=services)}
    assert checks["environment:lab"].level == "ok", checks["environment:lab"].detail
    assert "host key verified" in checks["environment:lab"].detail
    assert [r.actor for r in await audit_rows(services, "net.ssh")][-1] == "operator"
    await pool_for(services).close()


async def test_flag_off_host_files_do_not_exist_for_the_agent(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    host_file(services, ssh_server)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="*", action="allow")])
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert err and "unknown environment 'lab' (settings.environments: none configured)" in text
    text, err, _ = await tool(services, ctx, engine, "use_environment", {})
    assert not err and "lab" not in text
    assert not any(
        c.check.startswith("environment:") for c in await run_checks(services.settings, services=services)
    )
    for name in ("use_environment", "exec_command", "write_stdin"):
        assert KEY_NAME not in secret_names(services.harness.tools[name], services.settings, services.harness)
    assert not await audit_rows(services, "net.ssh") and not ssh_server.locals


async def test_legacy_entry_flag_off_is_not_gated_or_audited(
    services: Services, ssh_server: SshServer, tmp_path: Path, workdir: Path
) -> None:
    configure(services, ssh_server, tmp_path)
    services.settings.environments = {"box": NamedEnvironment(backend="ssh")}
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "box"})
    assert not err, text
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo plain"})
    assert not err and "plain" in text and ctx.state.ssh_granted == {}
    assert not await audit_rows(services, "net.ssh") and not await audit_rows(services, "net.ssh_grant")
    await pool_for(services).close()


async def test_settings_entry_shadows_a_host_file_and_doctor_warns(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server)
    services.settings.environments = {"lab": NamedEnvironment(backend="local", description="shadow")}
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine)
    text, err, meta = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert not err and meta["backend"] == "local" and meta["remote"] is False
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "nope"})
    assert err and "settings.environments: lab" in text and "hosts: lab" in text
    checks = {c.check: c for c in await run_checks(services.settings, services=services)}
    assert checks["environment:lab"].level == "ok" and "local (local)" in checks["environment:lab"].detail
    warn = checks["environment:lab (host file)"]
    assert warn.level == "warn" and "host file `lab` shadowed by settings.environments" in warn.detail
    assert not ssh_server.locals and not await audit_rows(services, "net.ssh")


async def test_gate_asks_every_session_and_always_or_yolo_never_persist(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server)
    engine = PermissionEngine(BASE, mode="yolo")
    ctx = session(services, workdir, engine, interactive=True)
    # no justification: refused before anyone is asked, and nothing connects
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert err and "justification" in text and not services.approvals.pending()
    text, err, _ = await tool(
        services, ctx, engine, "use_environment", {"name": "lab", "justification": "please"}
    )
    assert err and "justification" in text
    args = {"name": "lab", "justification": WHY}
    call = asyncio.create_task(tool(services, ctx, engine, "use_environment", args))
    asked = await answer(services, engine, "always")  # yolo did not pre-grant it
    assert (asked.permission, asked.pattern, asked.one_shot, asked.justification) == ("ssh", "lab", True, WHY)
    assert "127.0.0.1" in asked.detail and str(ssh_server.port) in asked.detail
    text, err, _ = await call
    assert not err and ctx.state.environment == "lab", text
    assert not any(r.permission == "ssh" for r in engine.approved)  # `always` was not remembered
    grant = (await audit_rows(services, "net.ssh_grant"))[0]
    assert grant.reason == "human" and len(grant.detail["justificationHash"]) == 16
    assert WHY not in json.dumps(grant.detail)  # the row has the hash, the text is kept 24 h
    stored = justifications.lookup(services.settings.home, grant.detail["grant_id"])
    assert stored is not None and stored["kind"] == "ssh" and stored["text"] == WHY
    # same session: the grant is cached, exec does not ask
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo one"})
    assert not err and "one" in text and not services.approvals.pending()
    # another session with the same engine asks again; a rejection is not cached
    other = session(services, workdir, engine, sid="s2", interactive=True)
    call = asyncio.create_task(tool(services, other, engine, "use_environment", args))
    await answer(services, engine, "reject")
    text, err, _ = await call
    assert err and "rejected" in text and other.state.environment is None and other.state.ssh_granted == {}
    call = asyncio.create_task(tool(services, other, engine, "use_environment", args))
    await answer(services, engine, "once")
    assert not (await call)[1]
    # a restarted session starts without grants
    restored = SessionState()
    restore_state(restored, [])
    assert restored.ssh_granted == {}
    await pool_for(services).close()


async def test_non_interactive_needs_an_allow_rule(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server)
    for mode in ("manual", "yolo", "acceptEdits", "smart"):
        engine = PermissionEngine(BASE, mode=mode)  # type: ignore[arg-type]
        ctx = session(services, workdir, engine, sid=f"s-{mode}")
        text, err, _ = await tool(
            services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY}
        )
        assert err and "non-interactive" in text and "permission rule" in text, (mode, text)
        assert ctx.state.environment is None
    denied = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="deny")])
    ctx = session(services, workdir, denied, interactive=True)
    text, err, _ = await tool(services, ctx, denied, "use_environment", {"name": "lab", "justification": WHY})
    assert err and "permission denied for ssh" in text
    bare = ToolContext(session_id="s9", cwd=workdir, home=services.settings.home)  # no engine: fail closed
    text, err, _ = await tool(services, bare, PermissionEngine(BASE), "use_environment", {"name": "lab"})
    assert err and "no permission engine" in text
    assert not ssh_server.locals and not await audit_rows(services, "net.ssh")
    assert not await audit_rows(services, "net.ssh_grant")


async def test_legacy_settings_entry_and_default_backend_are_gated_too(
    services: Services, ssh_server: SshServer, tmp_path: Path, workdir: Path
) -> None:
    flag(services)
    configure(services, ssh_server, tmp_path)
    services.settings.environments = {"box": NamedEnvironment(backend="ssh")}
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine)
    ctx.state.environment = "box"  # selected before the gate existed (or by a stale state)
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo never"})
    assert err and "ssh `box`" in text and "non-interactive" in text and not ssh_server.locals
    allowed = PermissionEngine([*BASE, Rule(permission="ssh", pattern="box", action="allow")])
    ctx = session(services, workdir, allowed, sid="s2")
    text, err, _ = await tool(services, ctx, allowed, "use_environment", {"name": "box"})
    assert not err, text
    text, err, meta = await tool(services, ctx, allowed, "exec_command", {"cmd": "cat", "yield_time_ms": 200})
    assert not err and meta.get("running")
    text, err, _ = await tool(
        services, ctx, allowed, "write_stdin", {"session_id": meta["session_id"], "chars": "x\n"}
    )
    assert not err and "x" in text
    rows = await audit_rows(services, "net.ssh_grant")
    assert [r.detail["name"] for r in rows] == ["box"] and rows[0].reason == "rule:ssh:box"
    await pool_for(services).close()
    # the default backend, when it is `ssh`, is gated under the pattern `default`
    services.settings.backends = {**services.settings.backends, "environment": "ssh"}
    ctx = session(services, workdir, allowed, sid="s3")
    text, err, _ = await tool(services, ctx, allowed, "exec_command", {"cmd": "echo never"})
    assert err and "ssh `default`" in text
    by_default = PermissionEngine([*BASE, Rule(permission="ssh", pattern="default", action="allow")])
    ctx = session(services, workdir, by_default, sid="s4")
    text, err, _ = await tool(services, ctx, by_default, "exec_command", {"cmd": "echo dflt"})
    assert not err and "dflt" in text, text
    assert (await audit_rows(services, "net.ssh"))[-1].detail["name"] == "default"
    await services.backend("environment").close()


async def test_editing_or_removing_a_host_file_invalidates_grant_and_socket(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine)
    assert not (await tool(services, ctx, engine, "use_environment", {"name": "lab"}))[1]
    first = pool_for(services).instances["lab"]
    host_file(services, ssh_server, user="other")  # same name, different target
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo edited"})
    assert not err and "edited" in text
    assert pool_for(services).instances["lab"] is not first
    assert len(await audit_rows(services, "net.ssh_grant")) == 2 and len(ctx.state.ssh_granted) == 2
    (services.settings.home / "hosts" / "lab.yaml").unlink()
    services.harness.reload()
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "echo gone"})
    assert err and "unknown environment 'lab'" in text and "lab" not in pool_for(services).instances
    await pool_for(services).close()


async def test_host_key_only_in_scope_of_the_environment_tools(
    services: Services, ssh_server: SshServer
) -> None:
    flag(services)
    host_file(services, ssh_server)
    tools = services.harness.tools
    for name in ("use_environment", "exec_command", "write_stdin"):
        assert KEY_NAME in secret_names(tools[name], services.settings, services.harness)
    for name in ("read", "bash", "web_fetch"):
        assert KEY_NAME not in secret_names(tools[name], services.settings, services.harness)
    backend_cls = services.harness.select_backend("environment", "ssh")
    assert backend_cls is not None
    over = hosts.manifest_to_override(services.harness.hosts["lab"].manifest, services.settings.home)
    inst = backend_cls.load()(apply_overrides(services.settings, over))
    scope = secret_names(tools["read"], services.settings, services.harness)
    with secret_scope("read", scope), pytest.raises(UnscopedSecretError):
        await inst.check()  # another tool's call cannot make the backend read the host key
    assert not ssh_server.locals


async def test_previous_key_connects_inside_its_grace_window(
    services: Services, ssh_server: SshServer, tmp_path: Path
) -> None:
    configure(services, ssh_server, tmp_path)
    newer = asyncssh.generate_private_key("ssh-ed25519")  # rotated locally, not yet on the server
    SecretStore(services.settings.home).rotate(KEY_NAME, newer.export_private_key().decode(), 3600)
    with pytest.raises(ShellError, match="authentication failed"):  # flag off: today's behaviour
        await backend(services).exec_command(ExecRequest("echo never"), session_id="s1")
    flag(services)
    b = backend(services)
    seen: list[dict[str, Any]] = []

    async def audit(detail: dict[str, Any]) -> None:
        seen.append(detail)

    b.net_audit = audit
    chunk = await b.exec_command(ExecRequest("echo prev-ok"), session_id="s1")
    assert chunk.exit_code == 0 and "prev-ok" in chunk.output
    assert seen == [{"mode": "ssh", "host": "127.0.0.1", "port": ssh_server.port}]  # one open, after auth
    await b.close()
    SecretStore(services.settings.home).rotate(
        KEY_NAME, "not a key", 3600
    )  # unusable current, valid #prev gone
    with pytest.raises(ShellError, match="authentication failed|not a valid private key") as exc:
        await backend(services).exec_command(ExecRequest("echo never"), session_id="s1")
    assert "BEGIN" not in str(exc.value)


async def test_keepalive_is_passed_only_when_set(
    services: Services, ssh_server: SshServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    real = asyncssh.connect

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(asyncssh, "connect", spy)
    configure(services, ssh_server, tmp_path)
    b = backend(services)
    assert (await b.check()).ok
    await b.close()
    configure(services, ssh_server, tmp_path, keepalive_interval_s=12)
    b = backend(services)
    assert (await b.check()).ok
    await b.close()
    assert "keepalive_interval" not in seen[0] and seen[1]["keepalive_interval"] == 12
    for kwargs in seen:  # the hardening options never change
        assert kwargs["agent_path"] is None and kwargs["config"] is None and kwargs["known_hosts"]
        assert kwargs["preferred_auth"] == "publickey" and kwargs["password"] is None


async def test_changed_host_key_fails_closed_at_runtime_without_prompting(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server, trust=False)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    hosts.trust(services.settings.home, "127.0.0.1", ssh_server.port, other)
    before = hosts.known_hosts_path(services.settings.home).read_text()
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine, interactive=True)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert err and "host key does not match" in text and "known_hosts" in text and "lab" in text
    assert ctx.state.environment is None and not services.approvals.pending() and not ssh_server.locals
    assert hosts.known_hosts_path(services.settings.home).read_text() == before
    assert not await audit_rows(services, "net.ssh") and not await audit_rows(services, "host.trust")
    await pool_for(services).close()


async def test_untrusted_host_cannot_be_trusted_from_the_agent_side(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server, trust=False)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="*", action="allow")], mode="yolo")
    ctx = session(services, workdir, engine, interactive=True)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert err and "known_hosts file not found" in text and not services.approvals.pending()
    hosts.known_hosts_path(services.settings.home).parent.mkdir(parents=True, exist_ok=True)
    hosts.known_hosts_path(services.settings.home).write_text("")
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert err and "host key" in text and not services.approvals.pending() and not ssh_server.locals
    assert hosts.known_hosts_path(services.settings.home).read_text() == ""
    await pool_for(services).close()


async def test_cli_test_trusts_on_first_use_and_refuses_a_changed_key(
    services: Services, ssh_server: SshServer
) -> None:
    st, home = services.settings, services.settings.home
    host_file(services, ssh_server, trust=False)  # the CLI works with the flag off
    said: list[str] = []
    asked: list[str] = []

    def confirm(yes: bool) -> Any:
        def ask(question: str) -> bool:
            asked.append(question)
            return yes

        return ask

    async def run(**kw: Any) -> int:
        said.clear()
        return await ssh_cli.run_test(
            st,
            "lab",
            record=services.audit.record,
            harness=services.harness,
            echo=said.append,
            timeout=5,
            **kw,
        )

    fp = ssh_server.host_key.get_fingerprint()
    assert await run() == 3 and "--fingerprint" in said[-1] and fp in said[0]  # no terminal, no pin
    assert await run(fingerprint="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == 3
    assert await run(confirm=confirm(False)) == 3 and len(asked) == 1
    assert hosts.known_keys(home, "127.0.0.1", ssh_server.port) == [] and not ssh_server.locals
    assert not await audit_rows(services, "host.trust")
    assert await run(confirm=confirm(True)) == 0 and "round trip passed" in said[-1]
    rows = await audit_rows(services, "host.trust")
    assert len(rows) == 1 and rows[0].actor == "operator" and rows[0].reason == "confirmed"
    want = {"name": "lab", "host": "127.0.0.1", "port": ssh_server.port, "fingerprint": fp, "replaced": False}
    assert want.items() <= rows[0].detail.items() and rows[0].detail["key_type"] == "ssh-ed25519"
    opened = await audit_rows(services, "net.ssh")
    assert len(opened) == 1 and opened[0].actor == "operator" and opened[0].detail["name"] == "lab"
    asked.clear()
    assert await run() == 0 and not asked  # already trusted: no question
    assert len(await audit_rows(services, "host.trust")) == 1
    # the server now presents a key that differs from known_hosts: an error, never a question
    other = asyncssh.generate_private_key("ssh-ed25519")
    hosts.trust(home, "127.0.0.1", ssh_server.port, other.export_public_key().decode().strip(), replace=True)
    ran = len(ssh_server.locals)
    assert await run(confirm=confirm(True)) == 3 and not asked
    assert "HOST KEY CHANGED" in said[-1] and other.get_fingerprint() in said[-1] and fp in said[-1]
    assert await run(fingerprint=fp) == 3 and len(ssh_server.locals) == ran  # a pin is not a replace
    assert len(await audit_rows(services, "host.trust")) == 1
    assert await run(replace_key=True) == 3  # still needs the operator
    assert await run(replace_key=True, confirm=confirm(True)) == 0 and len(asked) == 1
    rows = await audit_rows(services, "host.trust")
    assert len(rows) == 2 and rows[1].detail["replaced"] is True and rows[1].detail["fingerprint"] == fp
    assert hosts.known_keys(home, "127.0.0.1", ssh_server.port) == [
        ssh_server.host_key.export_public_key().decode().strip()
    ]
    assert await run() == 0
    assert await ssh_cli.run_test(st, "nope", record=services.audit.record, echo=said.append) == 2


async def test_cli_test_reports_an_unreachable_host(services: Services, ssh_server: SshServer) -> None:
    host_file(services, ssh_server, trust=False, port=1)
    said: list[str] = []
    code = await ssh_cli.run_test(
        services.settings, "lab", record=services.audit.record, harness=services.harness, echo=said.append
    )
    assert code == 1 and "lab" in said[-1] and not await audit_rows(services, "host.trust")


async def test_no_key_material_in_audit_events_host_files_or_tool_output(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    flag(services)
    host_file(services, ssh_server)
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine)
    outputs = [
        (await tool(services, ctx, engine, "use_environment", {"name": "lab"}))[0],
        (await tool(services, ctx, engine, "use_environment", {}))[0],
        (await tool(services, ctx, engine, "exec_command", {"cmd": "echo done"}))[0],
    ]
    said: list[str] = []
    await ssh_cli.run_test(
        services.settings, "lab", record=services.audit.record, harness=services.harness, echo=said.append
    )
    private = ssh_server.user_key.export_private_key().decode()
    body = "".join(private.splitlines()[1:-1])
    needles = [body[:40], body[-40:], "PRIVATE KEY"]
    async with services.db.session() as s:
        audit = [json.dumps(r.detail) + r.reason for r in (await s.execute(select(AuditRow))).scalars()]
        events = [json.dumps(r.data) for r in (await s.execute(select(EventRow))).scalars()]
    files = [p.read_text() for p in (services.settings.home / "hosts").iterdir()]
    files.append(hosts.known_hosts_path(services.settings.home).read_text())
    haystack = "\n".join([*audit, *events, *files, *outputs, *said])
    assert audit and all(n not in haystack for n in needles)
    assert KEY_NAME in haystack  # names are fine, values never
    await pool_for(services).close()
