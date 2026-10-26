"""G7 endpoints over the ASGI app: session plan, per-session sources, usage timeseries,
question validation (422) and bulk approve (`cascade`)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def _idle(client: httpx.AsyncClient, sid: str) -> None:
    for _ in range(300):
        idle = (await client.get(f"/api/sessions/{sid}")).json()["status"] == "idle"
        msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
        if idle and msgs and msgs[-1]["role"] == "assistant" and not msgs[-1].get("tool_calls"):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("session did not go idle")


async def test_plan_endpoint_follows_update_plan(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    assert (await client.get(f"/api/sessions/{sid}/plan")).json() == {
        "steps": [],
        "explanation": "",
        "plan_mode": False,
    }
    steps = [{"step": "read code", "status": "completed"}, {"step": "fix bug", "status": "in_progress"}]
    gateway.queue(
        Scripted(tool_calls=[("update_plan", {"steps": steps, "explanation": "two steps"})]),
        Scripted(text="planned"),
    )
    await client.post("/api/chat", json={"session_id": sid, "message": "plan it"})
    await _idle(client, sid)
    plan = (await client.get(f"/api/sessions/{sid}/plan")).json()
    assert [s["step"] for s in plan["steps"]] == ["read code", "fix bug"] and plan[
        "explanation"
    ] == "two steps"
    assert (await client.get("/api/sessions/nope/plan")).status_code == 404


async def test_session_sources_round_trip(client: httpx.AsyncClient, services: Services) -> None:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    got = (await client.get(f"/api/sessions/{sid}/sources")).json()
    assert got["session_id"] == sid and got["disabled"] == []
    assert all(s["enabled"] for s in got["sources"])
    put = await client.put(f"/api/sessions/{sid}/sources", json={"disabled": ["mcp:docs", "plugin:echo"]})
    assert put.status_code == 200 and put.json()["disabled"] == ["mcp:docs", "plugin:echo"]
    bad = await client.put(f"/api/sessions/{sid}/sources", json={"disabled": ["bash"]})
    assert bad.status_code == 422
    assert (await client.get("/api/sessions/nope/sources")).status_code == 404
    events = await services.events.replay(0, frozenset({f"session:{sid}"}))
    assert [e.data for e in events if e.type == "session.sources_changed"] == [
        {"disabled": ["mcp:docs", "plugin:echo"]}
    ]


async def test_usage_timeseries_and_budget(client: httpx.AsyncClient, services: Services) -> None:
    services.settings.max_cost_usd = 2.5
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post("/api/chat", json={"session_id": sid, "message": "hi"})
    await _idle(client, sid)
    body = (await client.get("/api/usage/timeseries?days=1")).json()
    assert body["bucket"] == "hour" and len(body["points"]) >= 24
    assert sum(p["calls"] for p in body["points"]) >= 1
    assert body["budget"]["max_cost_usd"] == 2.5
    assert body["sessions"][0]["session_id"] == sid and body["sessions"][0]["ratio"] == 0.0
    week = (await client.get("/api/usage/timeseries?days=7")).json()
    assert week["bucket"] == "day"


async def test_question_answer_validation_is_422(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    q = {"question": "Strict?", "options": [{"label": "yes"}, {"label": "no"}], "allowCustom": False}
    gateway.queue(Scripted(tool_calls=[("ask_user_question", {"questions": [q]})]), Scripted(text="ok"))
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    await client.post("/api/chat", json={"session_id": sid, "message": "ask"})
    pending: list[dict] = []  # type: ignore[type-arg]
    for _ in range(200):
        pending = (await client.get(f"/api/questions/pending?session_id={sid}")).json()
        if pending:
            break
        await asyncio.sleep(0.02)
    rid = pending[0]["request_id"]
    assert pending[0]["timeout_s"] == 60
    bad = await client.post(f"/api/questions/{rid}/answer", json={"responses": [{"customText": "maybe"}]})
    assert bad.status_code == 422
    ok = await client.post(f"/api/questions/{rid}/answer", json={"responses": [{"answers": ["yes"]}]})
    assert ok.status_code == 200
    await _idle(client, sid)


async def test_permission_cascade_over_rest(client: httpx.AsyncClient, services: Services) -> None:
    ids = []
    for _ in range(2):
        sid = (await client.post("/api/sessions", json={})).json()["id"]
        await client.post(
            "/api/chat", json={"session_id": sid, "message": '/tool bash {"command": "echo hi"}'}
        )
        ids.append(sid)
    pending: list[dict] = []  # type: ignore[type-arg]
    for _ in range(300):
        pending = (await client.get("/api/permissions/pending")).json()
        if len(pending) == 2:
            break
        await asyncio.sleep(0.02)
    assert len(pending) == 2 and all(p["one_shot"] is False for p in pending)
    r = await client.post(
        f"/api/permissions/{pending[0]['request_id']}/reply", json={"reply": "once", "cascade": True}
    )
    assert r.status_code == 200
    for sid in ids:
        await _idle(client, sid)
    assert (await client.get("/api/permissions/pending")).json() == []
