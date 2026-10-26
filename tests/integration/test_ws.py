"""WebSocket control plane against a real uvicorn server (ASGITransport has no WS)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted

Frame = dict[str, Any]


@asynccontextmanager
async def running_server(services: Services, settings: Settings) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings, services=services), log_level="warning"))
    serve = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        server.should_exit = True
        await serve


@pytest.fixture
async def url(services: Services, settings: Settings) -> AsyncIterator[str]:
    async with running_server(services, settings) as u:
        yield u


async def recv(ws: ClientConnection, timeout: float = 10) -> Frame:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))  # type: ignore[no-any-return]


async def recv_until(ws: ClientConnection, pred: Callable[[Frame], bool], timeout: float = 10) -> list[Frame]:
    frames: list[Frame] = []
    async with asyncio.timeout(timeout):
        while True:
            frame = json.loads(await ws.recv())
            frames.append(frame)
            if pred(frame):
                return frames


def is_type(t: str, **data: Any) -> Callable[[Frame], bool]:
    def pred(f: Frame) -> bool:
        return f.get("type") == t and all(f.get("data", {}).get(k) == v for k, v in data.items())

    return pred


async def hello(ws: ClientConnection, **fields: Any) -> Frame:
    await ws.send(json.dumps({"type": "hello", **fields}))
    connected = await recv(ws)
    assert connected["type"] == "connected"
    return connected


async def followup(ws: ClientConnection, sid: str, text: str, rid: str = "r1") -> None:
    msg = {"kind": "followup", "session_id": sid, "text": text}
    await ws.send(json.dumps({"type": "control_request", "request_id": rid, "request": msg}))


# ---------------------------------------------------------------------------- auth
async def test_auth_runs_before_accept(services: Services, settings: Settings) -> None:
    settings.server_password = "s3cret-pass"
    async with running_server(services, settings) as url:
        for bad in (url, f"{url}?token=wrong"):
            with pytest.raises(InvalidStatus) as exc:
                async with connect(bad):
                    pass
            assert exc.value.response.status_code == 403  # handshake refused (1008 pre-accept)
        async with connect(f"{url}?token=s3cret-pass") as ws:
            assert (await hello(ws))["server_seq"] >= 0
        async with connect(url, additional_headers={"Authorization": "Bearer s3cret-pass"}) as ws:
            await hello(ws)


async def test_browser_origin_must_be_loopback_or_allow_listed(
    services: Services, settings: Settings
) -> None:
    settings.cors_origins = ["https://ui.example.com"]
    async with running_server(services, settings) as url:
        with pytest.raises(InvalidStatus):
            async with connect(url, origin="https://evil.example.com"):  # type: ignore[arg-type]
                pass
        for ok in ("http://localhost:5173", "https://ui.example.com"):
            async with connect(url, origin=ok) as ws:  # type: ignore[arg-type]
                await hello(ws)


# ---------------------------------------------------------------- replay + live
async def test_hello_replays_then_streams_live_chat(url: str, services: Services) -> None:
    sess = await services.create_session(title="t")
    await services.coordinator.submit(sess.id, "before")
    await services.coordinator.wait(sess.id, 10)
    async with connect(url) as ws:
        connected = await hello(ws, last_seq=0, channels=[f"session:{sess.id}"])
        assert connected["generation"] >= 1 and connected["pending_permissions"] == []
        replay = await recv_until(ws, is_type("run.completed"))
        assert replay[0]["type"] == "session.created"
        assert replay[-1]["data"]["text"] == "echo: before"
        await followup(ws, sess.id, "live")
        live = await recv_until(ws, is_type("run.completed", text="echo: live"))
    acks = [f for f in live if f["type"] == "ack"]
    assert acks and acks[0]["ok"] and acks[0]["detail"].startswith("run_")
    events = [f for f in replay + live if "seq" in f]
    assert all(e["aggregate_id"] == sess.id for e in events)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))


async def test_no_hello_uses_query_params(url: str, services: Services) -> None:
    sess = await services.create_session(title="t")
    await services.coordinator.submit(sess.id, "q")
    await services.coordinator.wait(sess.id, 10)
    async with connect(f"{url}?last_seq=0&channels=session:{sess.id}") as ws:
        assert (await recv(ws))["type"] == "connected"
        frames = await recv_until(ws, is_type("run.completed"))
    assert frames[-1]["data"]["text"] == "echo: q"


async def test_subscribe_switches_channels_and_replays(url: str, services: Services) -> None:
    a = await services.create_session(title="a")
    b = await services.create_session(title="b")
    async with connect(url) as ws:
        await hello(ws, channels=[f"session:{a.id}"])
        sub = {"type": "subscribe", "channels": [f"session:{b.id}"], "last_seq": 0}
        await ws.send(json.dumps(sub))
        first = await recv(ws)
    assert first["type"] == "session.created" and first["aggregate_id"] == b.id


# ------------------------------------------------------------------- approvals
async def test_approval_round_trip_over_ws(url: str, services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo via-ws"})]), Scripted(text="ran"))
    sess = await services.create_session(title="t")
    async with connect(url) as ws:
        await hello(ws, channels=[f"session:{sess.id}"])
        await followup(ws, sess.id, "run it")
        frames = await recv_until(ws, is_type("permission.requested"))
        req = frames[-1]["data"]
        assert req["tool"] == "bash" and req["pattern"] == "echo via-ws"
        reply = {"kind": "permission", "reply": "once"}
        await ws.send(
            json.dumps({"type": "control_response", "request_id": req["request_id"], "response": reply})
        )
        frames = await recv_until(ws, is_type("run.completed"))
    assert {"type": "ack", "request_id": req["request_id"], "ok": True, "detail": None} in frames
    done = next(f for f in frames if f["type"] == "tool.completed")
    assert "via-ws" in done["data"]["content"] and not done["data"]["is_error"]


async def test_pending_approvals_are_resent_on_connect(
    url: str, services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo later"})]), Scripted(text="ok"))
    sess = await services.create_session(title="t")
    await services.coordinator.submit(sess.id, "go")
    for _ in range(200):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    async with connect(url) as ws:
        connected = await hello(ws)
        (pending,) = connected["pending_permissions"]
        assert pending["session_id"] == sess.id and pending["pattern"] == "echo later"
        await ws.send(
            json.dumps(
                {
                    "type": "control_response",
                    "request_id": pending["request_id"],
                    "response": {"kind": "permission", "reply": "reject"},
                }
            )
        )
        frames = await recv_until(ws, lambda f: f["type"] == "ack")
        assert frames[-1]["ok"]
    await services.coordinator.wait(sess.id, 10)


async def test_unknown_ids_ack_false_never_500(url: str, services: Services) -> None:
    async with connect(url) as ws:
        await hello(ws)
        reply = {"kind": "question", "responses": []}
        await ws.send(json.dumps({"type": "control_response", "request_id": "nope", "response": reply}))
        assert (await recv(ws)) == {
            "type": "ack",
            "request_id": "nope",
            "ok": False,
            "detail": "no pending request with that id",
        }
        await followup(ws, "ses_missing", "hi", rid="r9")
        ack = await recv(ws)
        assert ack["request_id"] == "r9" and not ack["ok"] and ack["detail"] == "session not found"


# ------------------------------------------------------------------ interrupt
async def test_interrupt_cancels_a_running_turn(url: str, services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "sleep 30"})]))
    services.settings.permission_mode = "manual"
    sess = await services.create_session(title="t")
    async with connect(url) as ws:
        await hello(ws, channels=[f"session:{sess.id}"])
        await followup(ws, sess.id, "slow")
        await recv_until(ws, is_type("permission.requested"))
        stop = {"kind": "interrupt", "session_id": sess.id}
        await ws.send(json.dumps({"type": "control_request", "request_id": "stop", "request": stop}))
        frames = await recv_until(ws, lambda f: f["type"] == "ack" and f["request_id"] == "stop")
        assert frames[-1] == {"type": "ack", "request_id": "stop", "ok": True, "detail": "cancelled"}
        assert any(f["type"] == "run.aborted" for f in frames)
    assert not services.coordinator.is_running(sess.id)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[-1]["role"] == "tool" and "aborted" in hist[-1]["content"]


# --------------------------------------------------------------- frame errors
async def test_unknown_invalid_and_binary_frames_get_errors(url: str, services: Services) -> None:
    async with connect(url) as ws:
        await hello(ws)
        await ws.send(json.dumps({"type": "launch_missiles", "request_id": "x"}))
        err = await recv(ws)
        assert err["type"] == "control_response" and err["subtype"] == "error" and err["request_id"] == "x"
        assert err["code"] == "unknown_type" and err["frame_type"] == "launch_missiles"
        await ws.send("{not json")
        assert (await recv(ws))["subtype"] == "invalid_frame"
        await ws.send(
            json.dumps({"type": "control_request", "request_id": "y", "request": {"kind": "steer"}})
        )
        bad = await recv(ws)
        assert bad["subtype"] == "invalid_frame" and bad["request_id"] == "y"
        await ws.send(b"\x00\x01binary")
        assert (await recv(ws))["subtype"] == "binary_dropped"
        # `\n`-joined batches are tolerated: two frames, two answers
        batch = json.dumps({"type": "nope1"}) + "\n" + json.dumps({"type": "nope2"})
        await ws.send(batch)
        msgs = [(await recv(ws))["error"] for _ in range(2)]
        assert "nope1" in msgs[0] and "nope2" in msgs[1]
    assert services.connections.binary_dropped == 1


# ------------------------------------------------------- keepalive + shutdown
async def test_ping_pong_and_keepalive_timeout(services: Services, settings: Settings) -> None:
    settings.ws_ping_interval_s = 0.2
    async with running_server(services, settings) as url:
        async with connect(url) as ws:
            await hello(ws)
            for _ in range(4):  # answering pings keeps the connection open past 2 intervals
                ping = await recv(ws, 3)
                assert ping["type"] == "ping" and ping["server_seq"] >= 0
                await ws.send(json.dumps({"type": "pong", "tick": ping["tick"]}))
        async with connect(url) as silent:
            await hello(silent)
            with pytest.raises(ConnectionClosed) as exc:
                while True:
                    await asyncio.wait_for(silent.recv(), 5)
            assert exc.value.rcvd is not None and exc.value.rcvd.code == 1011


async def test_backpressure_overflow_closes_1013(url: str, services: Services) -> None:
    async with connect(url) as ws:
        await hello(ws)
        for sub in list(services.bus._subs):
            sub.overflowed = True
        with pytest.raises(ConnectionClosed) as exc:
            while True:
                await asyncio.wait_for(ws.recv(), 5)
    assert exc.value.rcvd is not None and exc.value.rcvd.code == 1013


async def test_close_all_sends_going_away(url: str, services: Services) -> None:
    async with connect(url) as ws:
        await hello(ws)
        assert len(services.connections) == 1
        await services.connections.close_all()
        with pytest.raises(ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), 5)
    assert exc.value.rcvd is not None and exc.value.rcvd.code == 1001
    for _ in range(50):
        if services.bus.subscriber_count == 0:
            break
        await asyncio.sleep(0.02)
    assert services.bus.subscriber_count == 0
