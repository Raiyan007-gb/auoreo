"""`GET /api/environments`: backends, the exec-server, bridge peers — status, never secrets."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.remote.config import BridgePeer, RemoteEnvironment
from auoreo.remote.environments import _named, public_url
from auoreo.remote.exec_server import ExecServer
from tests.remote_helpers import serving

TOKEN = "env-list-token-value"
BRIDGE_KEY = "env-list-bridge-key"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXEC_T", TOKEN)
    monkeypatch.setenv("BRIDGE_K", BRIDGE_KEY)


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def _by(rows: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in rows if r["kind"] == kind}


async def test_lists_backends_exec_server_and_bridges(
    client: httpx.AsyncClient, services: Services, tmp_path: Path
) -> None:
    async with (
        serving(ExecServer(TOKEN, default_workdir=str(tmp_path)).app()) as exec_http,
        serving(create_app(services.settings, services=services)) as peer,
    ):
        exec_ws = exec_http.replace("http://", "ws://")
        services.settings.environment_remote = RemoteEnvironment(url=exec_ws, token="secret:EXEC_T")
        services.settings.bridges = {
            "live": BridgePeer(url=peer, secret="secret:BRIDGE_K"),
            "dead": BridgePeer(url="http://user:pw@127.0.0.1:9/?token=x", secret="secret:BRIDGE_K"),
        }
        r = await client.get("/api/environments")
    assert r.status_code == 200
    rows = r.json()
    assert (
        TOKEN not in r.text and BRIDGE_KEY not in r.text and "pw@" not in r.text and "token=x" not in r.text
    )

    assert "remote" in _by(rows, "backend")
    assert _by(rows, "backend")["remote"]["remote"] is True
    [remote] = _by(rows, "remote").values()
    assert remote["ok"] is True and remote["url"] == f"{exec_ws}" and "exec-server" in remote["detail"]

    bridges = _by(rows, "bridge")
    assert bridges["live"]["ok"] is True and bridges["live"]["detail"].startswith("auoreo ")
    assert bridges["dead"]["ok"] is False and bridges["dead"]["url"] == "http://127.0.0.1:9/"


async def test_unreachable_exec_server_is_a_status_not_an_error(
    client: httpx.AsyncClient, services: Services
) -> None:
    services.settings.environment_remote = RemoteEnvironment(url="ws://127.0.0.1:9", token="secret:EXEC_T")
    rows = (await client.get("/api/environments")).json()
    [remote] = _by(rows, "remote").values()
    assert remote["ok"] is False and "cannot reach" in remote["detail"]


def test_named_environments_via_getattr() -> None:
    svc: Any = SimpleNamespace(
        settings=SimpleNamespace(
            environments={"box": {"backend": "docker"}, "gpu": SimpleNamespace(backend="modal")}
        )
    )
    rows = {e.name: e for e in _named(svc)}
    assert rows["box"].backend == "environment/docker" and rows["gpu"].backend == "environment/modal"
    assert all(e.kind == "named" and e.ok is None for e in rows.values())
    assert _named(SimpleNamespace(settings=SimpleNamespace())) == []  # type: ignore[arg-type]


def test_public_url_strips_credentials() -> None:
    assert public_url("https://u:p@h.example:8443/x?token=1#f") == "https://h.example:8443/x"
    assert public_url("unix:///run/a.sock") == "unix:///run/a.sock"


def test_env_list_cli() -> None:
    res = CliRunner().invoke(cli, ["env", "list", "--json"])
    assert res.exit_code == 0, res.output
    assert '"kind": "backend"' in res.stdout
