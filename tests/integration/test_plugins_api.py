"""`/api/plugins` over the ASGI app with the echo fixture plugin installed in the runtime root."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.plugins.connectors import PairingStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo"
PY = f"{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture
async def svc(home: Path, workdir: Path, tmp_path: Path) -> AsyncIterator[Services]:
    shutil.copytree(FIXTURE, home / "plugins" / "echo")
    st = Settings(
        home=home,
        gateway="fake",
        plugins_python=PY,
        plugins_backoff_scale=0.0,
        plugins={"echo": {"log_path": str(tmp_path / "out.log")}},
    )
    s = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def client(svc: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(svc.settings, services=svc)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_list_stop_start_reload(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/plugins")
    assert r.status_code == 200
    rows = {p["name"]: p for p in r.json()}
    assert rows["echo"]["status"] == "running" and rows["echo"]["verify"] == "ok"
    assert "plugin__echo__echo" in rows["echo"]["tools"]
    assert rows["telegram"]["status"] == "disabled" and rows["discord"]["provides"] == ["delivery"]

    tools = {t["name"] for t in (await client.get("/api/harness/tools")).json()}
    assert "plugin__echo__echo" in tools

    r = await client.post("/api/plugins/echo/stop")
    assert r.json()["status"] == "stopped" and r.json()["tools"] == []
    r = await client.post("/api/plugins/echo/start")
    assert r.json()["status"] == "running"
    r = await client.post("/api/plugins/echo/reload")
    assert r.json()["status"] == "running"
    assert (await client.post("/api/plugins/nope/stop")).status_code == 404


async def test_skills_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/plugins/echo/skills")
    assert r.json() == [{"name": "echo-usage", "description": "How to echo", "body": "Call echo."}]


async def test_pair_endpoint(client: httpx.AsyncClient, svc: Services) -> None:
    code = PairingStore(svc.settings.home, "echo").request("chat-9", "bob")
    r = await client.post("/api/plugins/echo/pair", json={"code": code})
    assert r.json() == {"paired": True, "chat_id": "chat-9"}
    settings_json = json.loads((svc.settings.home / "settings.json").read_text())
    assert settings_json["plugins"]["echo"]["allowlist"] == ["chat-9"]
    assert (await client.post("/api/plugins/echo/pair", json={"code": code})).status_code == 404
    assert (await client.post("/api/plugins/echo/pair", json={"code": "12"})).status_code == 422


def test_cli_offline_list_add_pair_remove(home: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"AUOREO_PORT": "9"}  # nothing listens there → on-disk fallbacks
    assert runner.invoke(cli_app, ["plugin", "add", str(FIXTURE)], env=env).exit_code == 2  # H1b
    r = runner.invoke(cli_app, ["plugin", "add", str(FIXTURE), "--trust-local"], env=env)
    assert r.exit_code == 0, r.output
    assert (home / "plugins" / "echo" / "plugin.yaml").is_file()
    rows = {
        p["name"]: p for p in json.loads(runner.invoke(cli_app, ["plugin", "list", "--json"], env=env).output)
    }
    assert rows["echo"]["status"] == "quarantined"  # H1b: add → quarantine → verify → enable
    assert runner.invoke(cli_app, ["plugin", "enable", "echo"], env=env).exit_code == 1  # not verified
    r = runner.invoke(cli_app, ["plugin", "verify", "echo"], env=env)
    assert r.exit_code == 0, r.output
    assert runner.invoke(cli_app, ["plugin", "enable", "echo"], env=env).exit_code == 0
    r = runner.invoke(cli_app, ["plugin", "list", "--json"], env=env)
    assert r.exit_code == 0, r.output
    rows = {p["name"]: p for p in json.loads(r.output)}
    assert rows["echo"]["status"] == "stopped" and rows["echo"]["verify"] == "missing"
    assert rows["telegram"]["status"] == "disabled"

    code = PairingStore(home, "echo").request("c1", "u")
    r = runner.invoke(cli_app, ["plugin", "pair", "echo", code], env=env)
    assert r.exit_code == 0 and "paired chat c1" in r.output
    assert runner.invoke(cli_app, ["plugin", "pair", "echo", code], env=env).exit_code == 1

    r = runner.invoke(cli_app, ["plugin", "remove", "echo"], env=env)
    assert r.exit_code == 0 and not (home / "plugins" / "echo").exists()
    assert runner.invoke(cli_app, ["plugin", "remove", "telegram"], env=env).exit_code == 1
