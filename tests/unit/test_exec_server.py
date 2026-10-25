"""`auoreo exec-server`: handshake auth, exec/write_stdin round trip, server-side floors."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app as cli
from auoreo.remote.exec_server import DENIED, ExecServer, check_bind, cmd_hash
from auoreo.remote.rpc_client import ExecClient, ExecRemoteError
from tests.remote_helpers import serving

TOKEN = "t0ken-for-tests"


@pytest.fixture
async def url(tmp_path: Path) -> AsyncIterator[str]:
    server = ExecServer(TOKEN, default_workdir=str(tmp_path))
    async with serving(server.app()) as http:
        yield http.replace("http://", "ws://")


@pytest.fixture
async def client(url: str) -> AsyncIterator[ExecClient]:
    c = ExecClient(url, TOKEN, timeout=10)
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.parametrize("token", [None, "wrong"])
async def test_token_required_at_handshake(url: str, token: str | None) -> None:
    c = ExecClient(url, token or "", timeout=5)
    if token is None:
        c.token = ""
    with pytest.raises(ExecRemoteError) as err:
        await c.call("ping")
    assert err.value.code == 403
    await c.close()


async def test_ping(client: ExecClient) -> None:
    pong = await client.call("ping")
    assert pong["ok"] is True and pong["backend"] == "exec-local"


async def test_exec_and_write_stdin_round_trip(client: ExecClient, tmp_path: Path) -> None:
    first = await client.call(
        "exec_command",
        {"session_id": "s1", "cmd": "pwd; read line; echo got:$line", "yield_time_ms": 300},
    )
    assert first["exit_code"] is None and first["process_id"]
    assert str(tmp_path) in first["output"]
    done = await client.call(
        "write_stdin", {"process_id": first["process_id"], "chars": "hello\n", "yield_time_ms": 5000}
    )
    assert done["exit_code"] == 0 and "got:hello" in done["output"] and done["process_id"] is None


async def test_command_gets_scrubbed_env(client: ExecClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_API_KEY", "leak")
    out = await client.call(
        "exec_command",
        {"cmd": "echo key=${SOME_API_KEY:-unset} x=$X_OK", "env": {"X_OK": "1", "LD_PRELOAD": "/evil.so"}},
    )
    assert out["output"].strip() == "key=unset x=1"


async def test_floor_denied_server_side(client: ExecClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="auoreo.exec_server")
    with pytest.raises(ExecRemoteError) as err:
        await client.call("exec_command", {"cmd": "rm -rf /"})
    assert err.value.code == DENIED and "floor" in str(err.value)
    assert f"cmd_hash={cmd_hash('rm -rf /')}" in caplog.text
    assert "rm -rf" not in caplog.text  # only the hash is logged
    ok = await client.call("exec_command", {"cmd": "echo fine"})
    assert ok["exit_code"] == 0 and f"cmd_hash={cmd_hash('echo fine')}" in caplog.text


async def test_processes_are_connection_scoped(client: ExecClient, url: str) -> None:
    started = await client.call("exec_command", {"cmd": "sleep 5", "yield_time_ms": 50})
    other = ExecClient(url, TOKEN, timeout=5)
    try:
        with pytest.raises(ExecRemoteError, match="unknown process"):
            await other.call("write_stdin", {"process_id": started["process_id"], "chars": ""})
    finally:
        await other.close()
    await client.call("close_session", {"session_id": "default"})
    with pytest.raises(ExecRemoteError):
        await client.call("write_stdin", {"process_id": started["process_id"], "chars": ""})


async def test_unknown_method_and_bad_params(client: ExecClient) -> None:
    with pytest.raises(ExecRemoteError, match="unknown method"):
        await client.call("shell")
    with pytest.raises(ExecRemoteError, match="non-empty"):
        await client.call("exec_command", {"cmd": "  "})


def test_bind_rules() -> None:
    check_bind("127.0.0.1", allow_remote=False, token="t")
    check_bind("::1", allow_remote=False, token="t")
    with pytest.raises(ValueError, match="--allow-remote"):
        check_bind("0.0.0.0", allow_remote=False, token="t")
    with pytest.raises(ValueError, match="token"):
        check_bind("0.0.0.0", allow_remote=True, token=None)
    with pytest.raises(ValueError, match="token"):
        check_bind("127.0.0.1", allow_remote=False, token="")
    check_bind("0.0.0.0", allow_remote=True, token="t")


def test_cli_refuses_non_loopback_without_allow_remote() -> None:
    res = CliRunner().invoke(
        cli, ["exec-server", "--host", "0.0.0.0", "--port", "0"], env={"AUOREO_EXEC_TOKEN": "x"}
    )
    assert res.exit_code == 2 and "allow-remote" in res.output
    res = CliRunner().invoke(cli, ["exec-server", "--port", "0"], env={"AUOREO_EXEC_TOKEN": ""})
    assert res.exit_code == 2 and "token" in res.output
