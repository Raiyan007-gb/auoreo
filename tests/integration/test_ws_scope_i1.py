"""I1: a capability `/ws` connection sees and answers only its own session's pending
requests, and a cookie-authenticated socket closes with 4001 once its JWT expires
(spec §18 `--ws-auth`, §24.7)."""

from __future__ import annotations

import asyncio
import json
import time
import types

import pytest
from websockets.asyncio.client import connect

from auoreo.core import session_jwt
from auoreo.core.services import Services
from auoreo.core.session_jwt import tokens_for
from auoreo.core.settings import Settings
from tests.integration.test_ws import hello, recv_until, running_server

PW = "s3cret-pass"


async def _wait_pending(services: Services, n_perm: int, n_q: int) -> None:
    for _ in range(200):
        if len(services.approvals.pending()) >= n_perm and len(services.questions.pending()) >= n_q:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("pending requests never appeared")


async def test_capability_connection_only_sees_and_answers_its_own_session(
    services: Services, settings: Settings
) -> None:
    settings.server_password = PW
    settings.cli.ws_auth = "capability"
    mine = await services.create_session(title="mine")
    other = await services.create_session(title="other")
    tasks = [
        asyncio.create_task(services.approvals.request(mine.id, "bash", "bash", "ls", "ls")),
        asyncio.create_task(services.approvals.request(other.id, "bash", "bash", "rm x", "rm x")),
        asyncio.create_task(services.questions.ask(other.id, [{"question": "q?", "options": []}])),
    ]
    await _wait_pending(services, 2, 1)
    other_perm = services.approvals.pending(other.id)[0].request_id
    other_q = services.questions.pending(other.id)[0].request_id
    cap, _ = tokens_for(services).mint(ip="127.0.0.1", kind="cap", session_id=mine.id)
    try:
        async with running_server(services, settings) as url, connect(f"{url}?token={cap}") as ws:
            connected = await hello(ws)
            assert {p["session_id"] for p in connected["pending_permissions"]} == {mine.id}
            assert connected["pending_questions"] == []
            reply = {"kind": "permission", "reply": "once"}
            await ws.send(
                json.dumps({"type": "control_response", "request_id": other_perm, "response": reply})
            )
            ack = (await recv_until(ws, lambda f: f.get("type") == "ack"))[-1]
            assert ack["ok"] is False and "scoped" in ack["detail"]
            answer = {"kind": "question", "responses": []}
            await ws.send(json.dumps({"type": "control_response", "request_id": other_q, "response": answer}))
            ack = (await recv_until(ws, lambda f: f.get("type") == "ack"))[-1]
            assert ack["ok"] is False and "scoped" in ack["detail"]
            assert other_perm in {p.request_id for p in services.approvals.pending()}
            assert other_q in {q.request_id for q in services.questions.pending()}
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_unscoped_connection_still_sees_everything(services: Services, settings: Settings) -> None:
    a = await services.create_session(title="a")
    b = await services.create_session(title="b")
    tasks = [
        asyncio.create_task(services.approvals.request(a.id, "bash", "bash", "ls", "ls")),
        asyncio.create_task(services.approvals.request(b.id, "bash", "bash", "ls", "ls")),
    ]
    await _wait_pending(services, 2, 0)
    try:
        async with running_server(services, settings) as url, connect(url) as ws:
            connected = await hello(ws)
            assert {p["session_id"] for p in connected["pending_permissions"]} == {a.id, b.id}
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_cookie_websocket_closes_4001_when_the_jwt_expires(
    services: Services, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.server_password = PW
    settings.cli.ws_auth = "token"
    settings.ws_ping_interval_s = 0.1
    token, _ = tokens_for(services).mint(ip="127.0.0.1")
    async with (
        running_server(services, settings) as url,
        connect(url, additional_headers={"Cookie": f"auoreo_token={token}"}) as ws,
    ):
        await hello(ws)
        real = time.time
        monkeypatch.setattr(session_jwt, "time", types.SimpleNamespace(time=lambda: real() + 7200))
        with pytest.raises(Exception):  # noqa: B017 - ConnectionClosed with 4001
            for _ in range(50):
                await asyncio.wait_for(ws.recv(), 2)
        assert ws.close_code == 4001
