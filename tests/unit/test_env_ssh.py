"""ssh environment backend against an in-process asyncssh server.

The server authenticates one generated user key, presents a generated host key and runs
each channel's command with a real local `bash -c` (stdin/stdout piped through), so the
backend's exec, stdin, yield/poll, PTY request, SFTP and fail-closed paths run for real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.backends.environment import ExecRequest
from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from auoreo.core.settings import NamedEnvironment, SshEnvironment
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext
from auoreo.tools.environments import pool_for
from auoreo.tools.shell_sessions import ShellError

asyncssh = pytest.importorskip("asyncssh")

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
