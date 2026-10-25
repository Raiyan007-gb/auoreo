"""G1a WebSocket: ping→pong, JSON-array batches (≤32), subscribed/unsubscribed replies,
`job:` channels, `edit` of a queued followup, the bounded inbox (429), close codes 4001/4401."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.inbox import InboxFull
from tests.integration.test_ws import followup, hello, is_type, recv, recv_until, running_server, url  # noqa: F401


def ctl(rid: str, **request: object) -> str:
    return json.dumps({"type": "control_request", "request_id": rid, "request": request})


async def test_client_ping_gets_immediate_pong(url: str) -> None:  # noqa: F811
    async with connect(url) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "ping", "tick": 41}))
        pong = await recv(ws, 2)
        assert pong["type"] == "pong" and pong["tick"] == 41 and pong["server_seq"] >= 0


async def test_json_array_batches(url: str) -> None:  # noqa: F811
    async with connect(url) as ws:
        await hello(ws)
        batch = [
            {"type": "ping", "tick": 1},
            {"type": "subscribe", "channels": ["server"]},
            {"type": "ping", "tick": 2},
        ]
        await ws.send(json.dumps(batch))
        frames = [await recv(ws) for _ in range(3)]
        assert [f["type"] for f in frames] == ["pong", "subscribed", "pong"]
        assert [frames[0]["tick"], frames[2]["tick"]] == [1, 2]
        await ws.send(json.dumps([{"type": "ping", "tick": i} for i in range(33)]))
        err = await recv(ws)
        assert err["type"] == "error" and err["subtype"] == "invalid_frame" and err["code"] == -32600
        await ws.send("[]")
        assert (await recv(ws))["code"] == -32600


async def test_subscribe_unsubscribe_and_job_channel(url: str, services: Services) -> None:  # noqa: F811
    async with connect(url) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "subscribe", "channels": ["job:nightly", "server"]}))
        sub = await recv(ws)
        assert sub["type"] == "subscribed" and sub["channels"] == ["job:nightly", "server"]
        await ws.send(json.dumps({"type": "unsubscribe", "channels": ["server"]}))
        unsub = await recv(ws)
        assert unsub == {"type": "unsubscribed", "channels": ["server"], "remaining": ["job:nightly"]}
        await services.emit("server", "cron.fired", {"job": "other"})
        await services.emit("server", "cron.fired", {"job": "nightly"})
        ev = await recv(ws)
        assert ev["type"] == "cron.fired" and ev["data"]["job"] == "nightly"
        await ws.send(json.dumps({"type": "subscribe", "channels": ["bogus:1"]}))
        err = await recv(ws)
        assert err["type"] == "error" and err["subtype"] == "unknown_channel"


async def test_edit_queued_followup_and_queue_bound(
    url: str,  # noqa: F811
    services: Services,
    gateway: FakeGateway,
) -> None:
    services.settings.permission_mode = "manual"
    services.settings.http.queue_bound = 1
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo hi"})]))
    sess = await services.create_session(title="t")
    async with connect(url) as ws:
        await hello(ws, channels=[f"session:{sess.id}"])
        await followup(ws, sess.id, "first", rid="f1")  # blocks on the approval
        await recv_until(ws, is_type("permission.requested"))
        await followup(ws, sess.id, "second", rid="f2")  # queued
        ack2 = await recv_until(ws, lambda f: f.get("request_id") == "f2")
        assert ack2[-1]["ok"] is True
        await followup(ws, sess.id, "third", rid="f3")  # over the bound
        ack3 = await recv_until(ws, lambda f: f.get("request_id") == "f3")
        assert ack3[-1]["ok"] is False and ack3[-1]["detail"].startswith("429")
        await ws.send(ctl("e1", kind="edit", session_id=sess.id, target="f2", text="second (edited)"))
        edited = await recv_until(ws, lambda f: f.get("request_id") == "e1")
        assert edited[-1]["ok"] is True
        rt = await services.coordinator.runtime(sess.id)
        assert [r.text for r in rt.inbox.next_turn] == ["second (edited)"]
        await ws.send(ctl("e2", kind="edit", session_id=sess.id, target="f1", text="too late"))
        late = await recv_until(ws, lambda f: f.get("request_id") == "e2")
        assert late[-1]["ok"] is False  # already running
    with pytest.raises(InboxFull):
        await services.coordinator.submit(sess.id, "fourth")
    await services.coordinator.cancel(sess.id)


async def test_chat_overflow_is_429_with_retry_after(
    services: Services, settings: Settings, gateway: FakeGateway
) -> None:
    settings.permission_mode = "manual"
    settings.http.queue_bound = 1
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo hi"})]))
    sess = await services.create_session(title="t")
    app = create_app(settings, services=services)
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app), httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        assert (await c.post("/api/chat", json={"session_id": sess.id, "message": "a"})).status_code == 202
        for _ in range(100):
            if services.coordinator.is_running(sess.id) and services.approvals.pending():
                break
            await asyncio.sleep(0.05)
        assert (await c.post("/api/chat", json={"session_id": sess.id, "message": "b"})).status_code == 202
        r = await c.post("/api/chat", json={"session_id": sess.id, "message": "c"})
        assert r.status_code == 429 and r.headers["retry-after"] == "2"
        assert r.json()["detail"]["retry_after_s"] == 2
    await services.coordinator.cancel(sess.id)


async def test_close_4401_when_password_changes(services: Services, settings: Settings) -> None:
    settings.server_password = "old-pass"
    settings.ws_ping_interval_s = 0.2
    async with running_server(services, settings) as u, connect(f"{u}?token=old-pass") as ws:
        await hello(ws)
        settings.server_password = "new-pass"
        with pytest.raises(ConnectionClosed) as exc:
            while True:
                await asyncio.wait_for(ws.recv(), 5)
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4401


async def test_close_4001_when_auth_expires(services: Services, settings: Settings) -> None:
    settings.ws_ping_interval_s = 0.2
    settings.http.ws_auth_ttl_s = 0.3
    async with running_server(services, settings) as u, connect(u) as ws:
        await hello(ws)
        with pytest.raises(ConnectionClosed) as exc:
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if frame.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong", "tick": frame["tick"]}))
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4001


async def test_wrong_host_rejects_ws_handshake(services: Services, settings: Settings) -> None:
    async with running_server(services, settings) as u:
        with pytest.raises(Exception):  # noqa: B017 - the handshake is refused
            async with connect(u, additional_headers={"Host": "evil.com"}):
                pass
