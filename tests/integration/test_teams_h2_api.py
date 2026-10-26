"""H2 API: delegation pause toggle, subagents listing, `/api/mailbox` alias, `POST /api/cron`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import Scripted
from auoreo.teams.service import TeamsError
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_delegation_pause_toggle_and_subagents(
    client: httpx.AsyncClient, services: Services, gateway: RoutedGateway
) -> None:
    r = await client.get("/api/delegation")
    assert r.status_code == 200 and r.json() == {
        "paused": False,
        "source": "settings",
        "running": 0,
        "queued": 0,
    }
    r = await client.put("/api/delegation/pause", json={"paused": True})
    assert r.status_code == 200 and r.json()["paused"] is True and r.json()["source"] == "api"
    teams = services.ext["teams"]
    parent = await new_parent(services)
    with pytest.raises(TeamsError, match="paused"):
        await teams.spawn(parent, agent="default", prompt="x", name="nope", background=True)
    r = await client.put("/api/delegation/pause", json={"paused": False})
    assert r.json()["paused"] is False

    gateway.route("CHILD-SUB", Scripted(text="sub done"))
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-SUB go", name="sub", background=False)
    r = await client.get(f"/api/sessions/{parent}/subagents")
    assert r.status_code == 200
    [row] = r.json()
    assert row["subagent_id"] == kid.session_id and row["role"] == "leaf" and row["status"] == "completed"
    assert (await client.get("/api/sessions/nope/subagents")).status_code == 404
    paused = [
        e.data
        for e in await services.events.replay(0, frozenset({"server"}))
        if e.type == "delegation.paused"
    ]
    assert [d["paused"] for d in paused] == [True, False]


async def test_mailbox_alias_route(
    client: httpx.AsyncClient, services: Services, gateway: RoutedGateway
) -> None:
    teams = services.ext["teams"]
    parent = await new_parent(services)
    gateway.route("CHILD-MAIL", Scripted(text="mail kid"))
    await teams.spawn(parent, agent="default", prompt="CHILD-MAIL", name="mk", background=False)
    team = await teams.team_of(parent)
    text, err = await call_tool(
        services, tool_ctx(services, parent), "send_message", {"to": "mk", "message": "hello kid"}
    )
    assert not err, text
    r = await client.get("/api/mailbox", params={"team_id": team.id})
    assert r.status_code == 200
    assert r.json() == (await client.get(f"/api/teams/{team.id}/mailbox")).json()
    assert any(m["text"] == "hello kid" and m["recipient"] == "mk" for m in r.json())
    assert (await client.get("/api/mailbox", params={"team_id": "nope"})).status_code == 404


async def test_post_cron_create_and_dry_run(client: httpx.AsyncClient, services: Services) -> None:
    body = {"name": "standup", "schedule": "0 9 * * 1-5", "prompt": "Summarize yesterday", "tz": "UTC"}
    r = await client.post("/api/cron", json={**body, "dry_run": True})
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["dry_run"] is True and plan["created"] is False and plan["kind"] == "cron"
    assert len(plan["next_fires"]) == 5 and all("T09:00:00" in f for f in plan["next_fires"])
    assert not (services.settings.home / "jobs" / "standup.yaml").exists()

    r = await client.post("/api/cron", json=body)
    assert r.status_code == 201 and r.json()["created"] is True
    assert (services.settings.home / "jobs" / "standup.yaml").is_file()
    names = [j["name"] for j in (await client.get("/api/cron")).json()]
    assert "standup" in names
    assert (await client.post("/api/cron", json=body)).status_code == 409
    bad = await client.post("/api/cron", json={**body, "name": "bad_one", "schedule": "99 * * * *"})
    assert bad.status_code == 422 and "invalid cron" in bad.text
