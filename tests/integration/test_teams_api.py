from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import Scripted
from tests.teams_helpers import RoutedGateway, new_parent, turn


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


async def test_teams_mailbox_and_usage_rollup_endpoints(
    client: httpx.AsyncClient, services: Services, gateway: RoutedGateway
) -> None:
    args = {"goal": "CHILD-API do it", "agent": "reviewer", "name": "rev"}
    gateway.route("PARENT-API", Scripted(tool_calls=[("delegate_task", args)]), Scripted(text="ok"))
    gateway.route("CHILD-API", Scripted(text="reviewed"))
    parent = await new_parent(services)
    assert (await turn(services, parent, "PARENT-API go")).status == "completed"

    r = await client.get("/api/teams")
    assert r.status_code == 200
    [team] = r.json()
    assert team["lead_session_id"] == parent and team["members"] == 1

    r = await client.get(f"/api/teams/{team['id']}")
    assert r.status_code == 200
    peers = {p["name"]: p for p in r.json()["peers"]}
    assert set(peers) == {"lead", "rev"}
    assert peers["rev"]["agent"] == "reviewer" and peers["rev"]["status"] == "done"
    assert peers["rev"]["running"] is False and peers["rev"]["depth"] == 1
    assert (await client.get("/api/teams/t_missing")).status_code == 404

    await services.ext["teams"].mail.send(team["id"], "lead", "rev", "thanks", "thanks")
    r = await client.get(f"/api/teams/{team['id']}/mailbox", params={"member": "rev"})
    assert r.status_code == 200
    assert [(m["sender"], m["recipient"], m["text"], m["read"]) for m in r.json()] == [
        ("lead", "rev", "thanks", True)
    ]

    child = peers["rev"]["session_id"]
    own = (await client.get("/api/usage", params={"session_id": parent})).json()
    rolled = (await client.get("/api/usage", params={"session_id": parent, "children": "true"})).json()
    assert {u["session_id"] for u in own} == {parent}
    assert {u["session_id"] for u in rolled} == {parent, child}
    assert len(rolled) == len(own) + 1


async def test_kanban_endpoints(client: httpx.AsyncClient, services: Services) -> None:
    r = await client.post("/api/kanban", json={"board": "ops", "title": "rotate keys", "body": "soon"})
    assert r.status_code == 201
    created = r.json()
    assert created["status"] == "ready" and created["board"] == "ops" and created["failures"] == 0
    await client.post("/api/kanban", json={"title": "default board task"})
    assert (await client.post("/api/kanban", json={"title": ""})).status_code == 422
    assert (await client.post("/api/kanban", json={"title": "x", "extra": 1})).status_code == 422

    await services.ext["teams"].kanban.claim("ops", "bot")
    r = await client.get("/api/kanban", params={"board": "ops"})
    assert [(t["title"], t["status"], t["worker"]) for t in r.json()] == [("rotate keys", "claimed", "bot")]
    r = await client.get("/api/kanban", params={"status": "ready"})
    assert [t["board"] for t in r.json()] == ["default"]
    assert len((await client.get("/api/kanban")).json()) == 2
    assert (await client.get("/api/kanban", params={"status": "bogus"})).status_code == 422
