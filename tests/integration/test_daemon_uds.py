"""The daemon's Unix socket: same app, 0600 socket, no token on UDS, CLI auto-connect."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.remote import daemon, uds
from tests.remote_helpers import serving

pytestmark = pytest.mark.skipif(not uds.supported(), reason="needs AF_UNIX")


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, server_password="s3cret")


@pytest.fixture
async def sock(services: Services, settings: Settings, home: Path) -> AsyncIterator[Path]:
    path = uds.socket_path(home)
    app = uds.TrustUnixSocket(create_app(settings, services=services), settings.server_password)
    async with serving(app, uds=path):
        yield path


def _client(path: Path) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(path)), base_url="http://auoreo")


async def test_health_and_session_over_socket_without_token(sock: Path) -> None:
    assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sock.parent).st_mode) == 0o700
    async with _client(sock) as c:
        health = await c.get("/api/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        created = await c.post("/api/sessions", json={"title": "over uds"})
        assert created.status_code == 201, created.text
        listed = await c.get("/api/sessions")
        assert [s["title"] for s in listed.json()] == ["over uds"]


async def test_tcp_still_needs_the_token(services: Services, settings: Settings) -> None:
    app = uds.TrustUnixSocket(create_app(settings, services=services), settings.server_password)
    async with serving(app) as base, httpx.AsyncClient(base_url=base) as c:
        assert (await c.get("/api/sessions")).status_code == 401
        ok = await c.get("/api/sessions", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


async def test_cli_transport_selection_picks_the_socket(
    sock: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await asyncio.to_thread(uds.daemon_socket, home) == sock
    monkeypatch.setenv(uds.NO_DAEMON_ENV, "1")
    assert await asyncio.to_thread(uds.daemon_socket, home) is None
    monkeypatch.delenv(uds.NO_DAEMON_ENV)

    runner = CliRunner()
    res = await asyncio.to_thread(runner.invoke, cli, ["run", "hello daemon", "--format", "json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["transport"] == f"unix:{sock}" and out["status"] == "completed"

    res = await asyncio.to_thread(runner.invoke, cli, ["session", "list", "--json"])
    assert res.exit_code == 0, res.output
    assert out["session_id"] in {r["id"] for r in json.loads(res.stdout)}


async def test_no_socket_means_in_process(home: Path) -> None:
    assert await asyncio.to_thread(uds.daemon_socket, home) is None
    st = await asyncio.to_thread(daemon.status, daemon.DaemonPaths(home))
    assert st["running"] is False and st["pid"] is None


def test_bind_refuses_a_regular_file(tmp_path: Path) -> None:
    f = tmp_path / "auoreo.sock"
    f.write_text("not a socket")
    with pytest.raises(OSError, match="not a socket"):
        uds.bind_unix(f)
    assert f.read_text() == "not a socket"


def test_stale_socket_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "d" / "auoreo.sock"
    first = uds.bind_unix(path)
    first.close()  # file stays, nobody listens
    second = uds.bind_unix(path)
    second.close()
    assert stat.S_ISSOCK(path.lstat().st_mode)


def test_daemon_start_status_stop(home: Path) -> None:
    paths = daemon.DaemonPaths(home)
    pid, health = daemon.start(paths, gateway="fake")
    try:
        assert health["status"] == "ok" and daemon.read_pid(paths) == pid
        assert stat.S_IMODE(os.stat(paths.sock).st_mode) == 0o600
        assert daemon.status(paths)["running"] is True
        with pytest.raises(daemon.DaemonError, match="already running"):
            daemon.start(paths, gateway="fake")
    finally:
        assert daemon.stop(paths) == pid
    assert not paths.pid.exists() and not paths.sock.exists()
    assert daemon.status(paths)["running"] is False and daemon.stop(paths) is None
