"""G5b web-UI helpers: `GET /api/rtc/capabilities` and `GET /api/sessions/{id}/context`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=20
        ) as c,
    ):
        yield c


async def test_rtc_capabilities_reports_teleop(client: httpx.AsyncClient, services: Services) -> None:
    r = await client.get("/api/rtc/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["teleop_input_enabled"] is False
    assert set(body) == {
        "available",
        "voice_enabled",
        "speak",
        "teleop_input_enabled",
        "livekit",
        "livekit_agent",
    }
    services.settings.teleop_input_enabled = True
    assert (await client.get("/api/rtc/capabilities")).json()["teleop_input_enabled"] is True


async def test_session_context_breakdown(client: httpx.AsyncClient) -> None:
    sid = (await client.post("/api/sessions", json={"title": "ctx"})).json()["id"]
    r = await client.get(f"/api/sessions/{sid}/context")
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["session_id"] == sid and c["context_window"] > 0
    assert c["system_tokens"] > 0 and c["tools_tokens"] >= 0 and c["messages_tokens"] == 0
    assert c["pressure"] >= c["system_tokens"]
    assert 0 < c["auto_compact_at"] <= c["context_window"]
    assert c["ratio"] == pytest.approx((c["pressure"] + c["tools_tokens"]) / c["context_window"], abs=1e-3)


async def test_session_context_unknown_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/sessions/nope/context")).status_code == 404


async def test_sessions_context_batch(client: httpx.AsyncClient) -> None:
    a = (await client.post("/api/sessions", json={"title": "ctx a"})).json()["id"]
    b = (await client.post("/api/sessions", json={"title": "ctx b"})).json()["id"]
    r = await client.get(
        "/api/context",
        params=[("session_id", a), ("session_id", b), ("session_id", "nope"), ("session_id", a)],
    )
    assert r.status_code == 200
    rows = r.json()
    assert [x["session_id"] for x in rows] == [a, b]  # unknown left out, duplicates once
    single = (await client.get(f"/api/sessions/{a}/context")).json()
    assert rows[0] == single
    assert (await client.get("/api/context", params=[("session_id", "x")] * 101)).status_code == 422
