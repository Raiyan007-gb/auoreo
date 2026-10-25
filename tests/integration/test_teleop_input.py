"""Teleop input over the `auoreo-input` data channel (F1): aiortc loopback, real server.

Disabled = 403 and no channel; enabled events run the `computer` tool (fake desktop backend)
through the normal executor path: an `ask` surfaces as a pending approval on the control
plane (never on the channel), floors still refuse lock/logout combos, 30 events/s per peer,
and typed text never reaches the audit log."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.rtc.teleop import InputError, to_computer_args
from tests.integration.test_rtc import Client, running_server, wait_for_event

pytest.importorskip("aiortc")


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(
        home=home,
        gateway="fake",
        approval_timeout_s=10,
        rtc_ice_servers=[],
        teleop_input_enabled=True,
        backends={"desktop": "fake"},
    )


@pytest.fixture
async def http(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        yield c


class InputClient(Client):
    def __init__(self) -> None:
        super().__init__(audio=False)
        self.input = self.pc.createDataChannel("auoreo-input")
        self.acks: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.raw: list[str] = []
        self.input.on("message", self._on_ack)

    def _on_ack(self, m: str) -> None:
        self.raw.append(m)
        self.acks.put_nowait(json.loads(m))

    async def opened(self) -> None:
        async with asyncio.timeout(10):
            while self.input.readyState != "open":
                await asyncio.sleep(0.02)

    def act(self, **ev: Any) -> None:
        self.input.send(json.dumps(ev))

    async def ack(self, timeout: float = 10) -> dict[str, Any]:
        async with asyncio.timeout(timeout):
            return await self.acks.get()


async def connect(http: httpx.AsyncClient, services: Services) -> tuple[InputClient, str, str]:
    sess = await services.create_session(title="teleop")
    client = InputClient()
    answer = await client.connect(http, session_id=sess.id, input=True)
    await client.opened()
    return client, sess.id, answer["rtc_id"]


def desktop(services: Services) -> Any:
    return services.backend("desktop")


async def audit_rows(http: httpx.AsyncClient, action: str) -> list[dict[str, Any]]:
    return list((await http.get("/api/audit", params={"action": action, "limit": 500})).json()["rows"])


def test_event_mapping() -> None:
    assert to_computer_args({"op": "click", "x": 500, "y": 250.4}) == {"op": "click", "target": "500,250"}
    assert to_computer_args({"op": "type", "text": "hi"}) == {"op": "type", "text": "hi"}
    assert to_computer_args({"op": "key", "key": "ctrl+s"}) == {"op": "key", "key": "ctrl+s"}
    assert to_computer_args({"op": "scroll", "x": 1, "y": 2, "direction": "up", "amount": 3}) == {
        "op": "scroll",
        "direction": "up",
        "target": "1,2",
        "amount": 3,
    }
    for bad in (
        {"op": "click", "x": 1001, "y": 0},
        {"op": "click"},
        {"op": "type"},
        {"op": "drag"},
        {"op": "move", "x": 1, "y": 1},
        {"op": "scroll", "direction": "sideways"},
    ):
        with pytest.raises(InputError):
            to_computer_args(bad)


async def test_disabled_is_403_and_channel_ignored(
    http: httpx.AsyncClient, services: Services, settings: Settings
) -> None:
    settings.teleop_input_enabled = False
    sess = await services.create_session(title="no")
    client = InputClient()
    try:
        await client.pc.setLocalDescription(await client.pc.createOffer())
        body = {"sdp": client.pc.localDescription.sdp, "type": "offer", "session_id": sess.id, "input": True}
        r = await http.post("/api/rtc/offer", json=body)
        assert r.status_code == 403
        assert services.rtc is None or not services.rtc.peers
    finally:
        await client.close()
    client = InputClient()  # without `input: true` the channel is not wired even if opened
    try:
        answer = await client.connect(http, session_id=sess.id)
        await client.opened()
        client.act(id="x", op="click", x=1, y=1)
        await asyncio.sleep(0.5)
        assert services.rtc is not None and services.rtc.peers[answer["rtc_id"]].teleop is None
        assert client.acks.empty() and desktop(services).actions == []
    finally:
        await client.close()


async def test_click_asks_on_the_control_plane_then_runs(http: httpx.AsyncClient, services: Services) -> None:
    client, sid, rtc_id = await connect(http, services)
    try:
        client.act(id="c1", op="click", x=500, y=500)
        asked = await wait_for_event(
            http, lambda e: e["type"] == "permission.requested" and e["aggregate_id"] == sid
        )
        assert asked["data"]["tool"] == "computer" and asked["data"]["pattern"] == "click"
        pending = (await http.get("/api/permissions/pending", params={"session_id": sid})).json()
        assert [p["request_id"] for p in pending] == [asked["data"]["request_id"]]
        assert client.raw == []  # nothing about the approval travels on the data channel
        r = await http.post(f"/api/permissions/{asked['data']['request_id']}/reply", json={"reply": "always"})
        assert r.status_code == 200
        ack = await client.ack()
        assert ack["id"] == "c1" and ack["ok"] is True and ack["detail"].startswith("click at 500,500")
        assert [a["op"] for a in desktop(services).actions] == ["click"]
        client.act(id="c2", op="click", x=10, y=990)  # "always" for click: no second prompt
        ack = await client.ack()
        assert (
            ack == {"id": "c2", "ok": True, "detail": ack["detail"]} and len(desktop(services).actions) == 2
        )
        assert all("request_id" not in m and "permission" not in m for m in client.raw)
        rows = await audit_rows(http, "tool.call")
        assert any(r["detail"]["tool"] == "computer" for r in rows)
        rejected = await http.get("/api/permissions/pending", params={"session_id": sid})
        assert rejected.json() == []
    finally:
        await client.close()


async def test_floor_refuses_lock_combo_and_typed_text_is_not_audited(
    http: httpx.AsyncClient, services: Services
) -> None:
    client, sid, rtc_id = await connect(http, services)
    try:
        runner = services.coordinator.runner
        rt = await services.coordinator.runtime(sid)
        engine = runner._engine(rt, runner._spec(rt, rt.agent, None))  # the engine teleop reuses
        engine.grant_always("computer", "key")
        engine.grant_always("computer", "type")
        client.act(id="k1", op="key", key="ctrl+alt+del")
        ack = await client.ack()
        assert ack["id"] == "k1" and ack["ok"] is False and "refused" in ack["detail"]
        client.act(id="t1", op="type", text="hunter2 secret words")
        ack = await client.ack()
        assert ack["ok"] is True
        assert [a["op"] for a in desktop(services).actions] == ["type"]  # the combo never reached it
        rows = await audit_rows(http, "teleop.input")
        assert [r["detail"]["op"] for r in rows] == ["key", "type"]
        assert rows[1]["detail"] == {"rtc_id": rtc_id, "op": "type", "text_chars": 20}
        everything = json.dumps((await http.get("/api/audit", params={"limit": 500})).json())
        assert "hunter2" not in everything
    finally:
        await client.close()


async def test_rate_limit_drops_and_counts(http: httpx.AsyncClient, services: Services) -> None:
    client, sid, rtc_id = await connect(http, services)
    try:
        for i in range(50):
            client.act(id=f"m{i}", op="move", x=i, y=i)  # unsupported: acked, never executed
        acks = [await client.ack() for _ in range(30)]
        await asyncio.sleep(0.3)
        assert client.acks.empty()
        assert [a["id"] for a in acks] == [f"m{i}" for i in range(30)]
        assert all(a["ok"] is False and "move" in a["detail"] for a in acks)
        assert services.rtc is not None and services.rtc.peers[rtc_id].teleop.dropped == 20
    finally:
        await client.close()
