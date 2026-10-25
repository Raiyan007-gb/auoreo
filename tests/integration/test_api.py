from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from tests.conftest import make_tool


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def parse_sse(text: str) -> list[dict]:  # type: ignore[type-arg]
    events = []
    for block in text.split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


async def wait_done(services: Services, sid: str) -> None:
    await services.coordinator.wait(sid, 10)
    await services.coordinator.background_idle(10)  # title generation


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["gateway"] == "fake"


async def test_chat_then_replay_events_and_resume(client: httpx.AsyncClient, services: Services) -> None:
    r = await client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 202
    sid = r.json()["session_id"]
    await wait_done(services, sid)
    r = await client.get("/api/events", params={"session_id": sid, "follow": "false"})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    everything = parse_sse(r.text)
    evs = [e for e in everything if e["type"] != "session.title_updated"]
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "session.created" and kinds[-1] == "run.completed"
    assert evs[-1]["data"]["text"] == "echo: hello"
    mid = evs[2]["seq"]
    r2 = await client.get(
        "/api/events", params={"session_id": sid, "follow": "false"}, headers={"Last-Event-ID": str(mid)}
    )
    assert [e["seq"] for e in parse_sse(r2.text)] == [e["seq"] for e in everything if e["seq"] > mid]


async def test_live_tail_streams_new_events_over_a_real_server(
    services: Services, settings: Settings
) -> None:
    """ASGITransport buffers responses, so live SSE is checked against real uvicorn."""
    import socket

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings, services=services), log_level="warning"))
    serve = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            sid = (await c.post("/api/sessions", json={})).json()["id"]
            received: list[dict] = []  # type: ignore[type-arg]

            async def listen() -> None:
                async with c.stream("GET", "/api/events", params={"session_id": sid}) as resp:
                    buf = ""
                    async for chunk in resp.aiter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            received.extend(parse_sse(block + "\n\n"))
                            if received and received[-1]["type"] == "run.completed":
                                return

            task = asyncio.create_task(listen())
            await asyncio.sleep(0.2)
            await c.post("/api/chat", json={"message": "live", "session_id": sid})
            await asyncio.wait_for(task, 10)
        assert received[-1]["data"]["text"] == "echo: live"
        assert [e["seq"] for e in received] == sorted(e["seq"] for e in received)
    finally:
        server.should_exit = True
        await serve


async def test_idempotency_key_dedupes(client: httpx.AsyncClient, services: Services) -> None:
    key = str(uuid.uuid4())
    r1 = await client.post("/api/chat", json={"message": "once"}, headers={"Idempotency-Key": key})
    r2 = await client.post("/api/chat", json={"message": "once"}, headers={"Idempotency-Key": key})
    assert r1.status_code == 202 and r2.status_code == 200
    assert r2.json()["deduped"] and r2.json()["session_id"] == r1.json()["session_id"]
    assert (
        await client.post("/api/chat", json={"message": "x"}, headers={"Idempotency-Key": "nope"})
    ).status_code == 400
    await wait_done(services, r1.json()["session_id"])


async def test_sessions_and_messages(client: httpx.AsyncClient, services: Services) -> None:
    r = await client.post("/api/sessions", json={"title": "t"})
    sid = r.json()["id"]
    assert r.status_code == 201
    await client.post("/api/chat", json={"message": "hi", "session_id": sid})
    await wait_done(services, sid)
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert (await client.get("/api/sessions/nope")).status_code == 404
    assert (await client.post("/api/sessions", json={"agent": "ghost"})).status_code == 404
    assert (await client.post("/api/sessions/nope/cancel")).json() == {"cancelled": False}


async def test_folder_defined_tool_hot_reload(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway, home: Path
) -> None:
    """The core promise: drop a folder, reload, the agent can use it — no code changes."""
    make_tool(
        home,
        "hello",
        "return 'hello ' + args['who']",
        extra="parameters:\n  type: object\n  properties: {who: {type: string}}\n  required: [who]\n",
    )
    r = await client.post("/api/harness/reload")
    assert r.json()["changes"]["tools"]["added"] == ["hello"]
    names = [t["name"] for t in (await client.get("/api/harness/tools")).json()]
    assert "hello" in names
    gateway.queue(Scripted(tool_calls=[("hello", {"who": "world"})]), Scripted(text="greeted"))
    sid = (await client.post("/api/chat", json={"message": "greet"})).json()["session_id"]
    await wait_done(services, sid)
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert msgs[2]["content"] == "hello world"


async def test_folder_defined_agent(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway, home: Path
) -> None:
    d = home / "agents" / "pirate"
    d.mkdir(parents=True)
    (d / "AGENT.md").write_text(
        "---\nname: pirate\ndescription: Talks like a pirate.\n"
        "tools: {allow: [read]}\n---\nAlways answer like a pirate.\n"
    )
    await client.post("/api/harness/reload")
    r = await client.post("/api/chat", json={"message": "ahoy", "agent": "pirate"})
    await wait_done(services, r.json()["session_id"])
    req = [r for r in gateway.requests if r.metadata.get("purpose") != "title"][-1]
    assert "like a pirate" in req.messages[0]["content"]
    assert [t["function"]["name"] for t in req.tools] == ["read"]


async def test_permission_reply_over_api(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo via-api"})]), Scripted(text="ok"))
    sid = (await client.post("/api/chat", json={"message": "run it"})).json()["session_id"]
    pending: list[dict] = []  # type: ignore[type-arg]
    for _ in range(100):
        pending = (await client.get("/api/permissions/pending", params={"session_id": sid})).json()
        if pending:
            break
        await asyncio.sleep(0.02)
    assert pending[0]["tool"] == "bash" and pending[0]["pattern"] == "echo via-api"
    r = await client.post(f"/api/permissions/{pending[0]['request_id']}/reply", json={"reply": "always"})
    assert r.json() == {"ok": True}
    await wait_done(services, sid)
    msgs = (await client.get(f"/api/sessions/{sid}/messages")).json()
    assert "via-api" in msgs[2]["content"]
    assert (await client.post("/api/permissions/nope/reply", json={"reply": "once"})).status_code == 404


async def test_auth_required_when_password_set(services: Services, settings: Settings) -> None:
    settings.server_password = "s3cret-pass"
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/api/health")).status_code == 200  # public
        assert (await c.get("/api/sessions")).status_code == 401
        ok = await c.get("/api/sessions", headers={"Authorization": "Bearer s3cret-pass"})
        assert ok.status_code == 200
        assert (await c.get("/api/sessions", params={"token": "s3cret-pass"})).status_code == 401
        r = await c.get("/api/events", params={"token": "s3cret-pass", "follow": "false"})
        assert r.status_code == 200


async def test_memory_endpoints(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/memory/facts", json={"content": "Project uses uv and ruff"})
    assert r.status_code == 201
    hits = (await client.get("/api/memory/search", params={"q": "which linter ruff"})).json()
    assert hits and "ruff" in hits[0]["text"]


async def test_routes_win_over_spa_mount(services: Services, settings: Settings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>")
    app = create_app(settings, services=services, web_dist=dist)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/api/health")).json()["status"] == "ok"
        assert "spa" in (await c.get("/")).text
        assert (await c.get("/openapi.json")).status_code == 200
