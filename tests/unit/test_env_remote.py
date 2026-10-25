"""`backends/environment/remote` against a live in-process exec-server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends.environment import ExecRequest
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.remote.exec_server import ExecServer
from tests.remote_helpers import serving

TOKEN = "remote-env-token"


@pytest.fixture
async def exec_url(tmp_path: Path) -> AsyncIterator[str]:
    async with serving(ExecServer(TOKEN, default_workdir=str(tmp_path)).app()) as http:
        yield http.replace("http://", "ws://")


def _backend(services: Services, settings: Settings) -> Any:
    bdef = services.harness.backends_for("environment")["remote"]
    return bdef.load()(settings)


async def test_remote_backend_round_trip(
    services: Services, home: Path, exec_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXEC_TOKEN_T", TOKEN)  # secret store: env → keyring → secrets.db
    st = Settings(
        home=home, gateway="fake", environment_remote={"url": exec_url, "token": "secret:EXEC_TOKEN_T"}
    )
    be = _backend(services, st)
    try:
        assert be.remote is True
        status = await be.check()
        assert status.ok and "exec-server" in status.detail
        chunk = await be.exec_command(ExecRequest(cmd="echo hi; exit 3"), session_id="s1")
        assert chunk.exit_code == 3 and chunk.output.strip() == "hi"
        live = await be.exec_command(
            ExecRequest(cmd="read a; echo you said $a", yield_time_ms=200), session_id="s1"
        )
        assert live.exit_code is None and live.process_id
        done = await be.write_stdin(live.process_id, "yo\n", yield_time_ms=5000)
        assert done.exit_code == 0 and "you said yo" in done.output
        await be.close_session("s1")
    finally:
        await be.close()


async def test_remote_backend_reports_bad_token_and_missing_config(
    services: Services, home: Path, exec_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXEC_TOKEN_T", "nope")
    st = Settings(
        home=home, gateway="fake", environment_remote={"url": exec_url, "token": "secret:EXEC_TOKEN_T"}
    )
    be = _backend(services, st)
    status = await be.check()
    await be.close()
    assert not status.ok and "403" in status.detail

    unconfigured = _backend(services, Settings(home=home, gateway="fake"))
    status = await unconfigured.check()
    assert not status.ok and "environment_remote" in status.detail


def test_settings_refuse_inline_secrets() -> None:
    with pytest.raises(ValueError, match="secret:"):
        Settings(environment_remote={"url": "ws://127.0.0.1:1", "token": "plain"})
    with pytest.raises(ValueError, match="secret:"):
        Settings(bridges={"b": {"url": "http://x", "secret": "plain"}})
