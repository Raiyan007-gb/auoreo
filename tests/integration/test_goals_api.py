"""`/api/goals` and `/api/suggestions` over HTTP (ASGI transport, fake gateway)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.suggestions import SuggestionEngine


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_goals_crud_and_absorb(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/goals", json={"title": "ship M6", "description": "gates green"})
    assert r.status_code == 201
    a = r.json()
    assert a["status"] == "active" and a["progress"] == 0 and a["evidence_session_ids"] == []
    b = (await client.post("/api/goals", json={"title": "release M6"})).json()

    r = await client.patch(f"/api/goals/{a['id']}", json={"progress": 0.4, "evidence_session_id": "ses_x"})
    assert (
        r.status_code == 200 and r.json()["progress"] == 0.4 and r.json()["evidence_session_ids"] == ["ses_x"]
    )
    assert (await client.patch(f"/api/goals/{a['id']}", json={"progress": 2})).status_code == 422
    assert (await client.patch("/api/goals/999", json={"progress": 0.1})).status_code == 404

    r = await client.post(f"/api/goals/{a['id']}/absorb", json={"into": b["id"]})
    assert r.status_code == 200
    assert r.json()["id"] == b["id"] and r.json()["evidence_session_ids"] == ["ses_x"]
    assert (await client.post(f"/api/goals/{a['id']}/absorb", json={"into": b["id"]})).status_code == 409

    r = await client.get("/api/goals", params={"status": "absorbed"})
    assert [g["id"] for g in r.json()] == [a["id"]] and r.json()[0]["absorbed_into"] == b["id"]
    assert [g["id"] for g in (await client.get("/api/goals", params={"status": "active"})).json()] == [
        b["id"]
    ]
    assert len((await client.get("/api/goals")).json()) == 2
    assert (await client.get(f"/api/goals/{b['id']}")).json()["title"] == "release M6"
    assert (await client.get("/api/goals", params={"status": "nope"})).status_code == 422

    r = await client.patch(f"/api/goals/{b['id']}", json={"status": "done", "progress": 1})
    assert r.json()["status"] == "done"


async def test_suggestions_propose_accept_deny(client: httpx.AsyncClient, services: Services) -> None:
    ses = (await client.post("/api/sessions", json={"title": "origin"})).json()
    body = {
        "title": "Run the tests",
        "kind": "goal",
        "origin_session": ses["id"],
        "action": {"type": "prompt", "text": "accepted via api"},
        "dedupe_key": "k1",
    }
    r = await client.post("/api/suggestions", json=body)
    assert r.status_code == 201 and r.json()["created"] is True
    sug = r.json()["suggestion"]
    assert sug["status"] == "pending" and sug["deliver"] == "origin"
    assert (await client.post("/api/suggestions", json=body)).json()["created"] is False
    assert (await client.post("/api/suggestions", json={"title": "x", "deliver": "mars"})).status_code == 422

    pending = (await client.get("/api/suggestions", params={"status": "pending"})).json()
    assert [s["id"] for s in pending] == [sug["id"]]

    r = await client.post(f"/api/suggestions/{sug['id']}/accept")
    assert r.status_code == 200
    out = r.json()
    assert out["suggestion"]["status"] == "accepted" and out["session_id"] == ses["id"] and out["run_id"]
    await services.coordinator.wait(ses["id"], 20)
    msgs = (await client.get(f"/api/sessions/{ses['id']}/messages")).json()
    assert any(m["role"] == "assistant" and m["content"] == "echo: accepted via api" for m in msgs)
    assert (await client.post(f"/api/suggestions/{sug['id']}/accept")).status_code == 409

    other = (await client.post("/api/suggestions", json={"title": "nope"})).json()["suggestion"]
    r = await client.post(f"/api/suggestions/{other['id']}/deny")
    assert r.status_code == 200 and r.json()["status"] == "denied"
    assert (await client.post("/api/suggestions/999/deny")).status_code == 404
    assert [s["id"] for s in (await client.get("/api/suggestions", params={"status": "denied"})).json()] == [
        other["id"]
    ]


async def test_suggestion_delivery_visible_in_deliveries(
    client: httpx.AsyncClient, services: Services
) -> None:
    r = await client.post("/api/suggestions", json={"title": "disk at 95%", "deliver": "home"})
    sid = r.json()["suggestion"]["id"]
    eng = services.ext["suggestions"]
    assert isinstance(eng, SuggestionEngine)
    await eng.drain()
    rows = (await client.get("/api/deliveries", params={"kind": "suggestion"})).json()
    assert [(d["status"], d["external_id"], d["target"]) for d in rows] == [
        ("delivered", f"suggestion-{sid}", "log")
    ]
    sug = (await client.get("/api/suggestions", params={"status": "pending"})).json()[0]
    assert sug["delivery_id"] == rows[0]["id"]
