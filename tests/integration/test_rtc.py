"""WebRTC signaling + `auoreo-events` data channel, loopback against a real uvicorn server.

The client is an in-process aiortc peer with a synthetic 440 Hz audio track. Tests that
need aiortc skip without the `rtc` extra; the 501 path runs everywhere.
"""

from __future__ import annotations

import asyncio
import fractions
import json
import math
import socket
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import uvicorn

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from tests.integration.test_api import parse_sse

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
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serve


@pytest.fixture
async def http(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    settings.rtc_ice_servers = []  # loopback: host candidates only
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        yield c


def sine_track() -> Any:
    from aiortc import AudioStreamTrack
    from av import AudioFrame

    class SineTrack(AudioStreamTrack):
        """20 ms frames of a 440 Hz sine at half scale, paced in real time."""

        rate = 48_000
        samples = 960

        def __init__(self) -> None:
            super().__init__()
            self._t = 0
            self._start: float | None = None

        async def recv(self) -> AudioFrame:
            import numpy as np

            if self._start is None:
                self._start = time.time()
            else:
                await asyncio.sleep(max(0.0, self._start + self._t / self.rate - time.time()))
            n = np.arange(self._t, self._t + self.samples)
            pcm = (0.5 * 32767 * np.sin(2 * math.pi * 440 * n / self.rate)).astype(np.int16)
            frame = AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = self.rate
            frame.pts = self._t
            frame.time_base = fractions.Fraction(1, self.rate)
            self._t += self.samples
            return frame

    return SineTrack()


class Client:
    """In-process aiortc peer: `auoreo-events` channel + optional sine audio."""

    def __init__(self, audio: bool = True) -> None:
        from aiortc import RTCConfiguration, RTCPeerConnection

        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # aiortc defaults to Google STUN
        self.channel = self.pc.createDataChannel("auoreo-events")
        self.inbox: asyncio.Queue[Frame] = asyncio.Queue()
        self.channel.on("message", lambda m: self.inbox.put_nowait(json.loads(m)))
        if audio:
            self.pc.addTrack(sine_track())

    async def connect(self, http: httpx.AsyncClient, **extra: Any) -> Frame:
        from aiortc import RTCSessionDescription

        await self.pc.setLocalDescription(await self.pc.createOffer())
        local = self.pc.localDescription
        r = await http.post("/api/rtc/offer", json={"sdp": local.sdp, "type": local.type, **extra})
        assert r.status_code == 201, r.text
        body: Frame = r.json()
        assert body["type"] == "answer" and body["rtc_id"].startswith("rtc_")
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type=body["type"]))
        return body

    def send(self, obj: Frame) -> None:
        self.channel.send(json.dumps(obj))

    async def recv_until(self, pred: Callable[[Frame], bool], timeout: float = 10) -> list[Frame]:
        frames: list[Frame] = []
        async with asyncio.timeout(timeout):
            while True:
                frame = await self.inbox.get()
                frames.append(frame)
                if pred(frame):
                    return frames

    async def close(self) -> None:
        await self.pc.close()


def is_type(t: str) -> Callable[[Frame], bool]:
    return lambda f: f.get("type") == t


async def wait_for_event(
    http: httpx.AsyncClient, pred: Callable[[Frame], bool], timeout: float = 10
) -> Frame:
    async with asyncio.timeout(timeout):
        while True:
            r = await http.get("/api/events", params={"follow": "false"})
            found = [e for e in parse_sse(r.text) if pred(e)]
            if found:
                return found[0]
            await asyncio.sleep(0.2)


# ---------------------------------------------------------------------- loopback
async def test_loopback_events_audio_and_close(http: httpx.AsyncClient, services: Services) -> None:
    pytest.importorskip("aiortc")
    sess = await services.create_session(title="rtc")
    client = Client()
    try:
        answer = await client.connect(http)
        rtc_id = answer["rtc_id"]
        connected = (await client.recv_until(is_type("connected")))[-1]
        assert connected["rtc_id"] == rtc_id and connected["server_seq"] >= 1

        client.send({"type": "subscribe", "channels": [f"session:{sess.id}"], "last_seq": 0})
        replay = await client.recv_until(is_type("session.created"))
        assert replay[-1]["aggregate_id"] == sess.id

        r = await http.post("/api/chat", json={"session_id": sess.id, "message": "over rtc"})
        assert r.status_code == 202
        frames = await client.recv_until(lambda f: f.get("type") == "run.completed")
        events = [f for f in frames if "seq" in f]
        kinds = [e["type"] for e in events]
        assert "run.started" in kinds and kinds.index("run.started") < kinds.index("run.completed")
        assert events[-1]["data"]["text"] == "echo: over rtc"
        assert all(e["aggregate_id"] == sess.id for e in events)
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert set(events[0]) == {"id", "seq", "type", "aggregate_id", "aggregate_seq", "data", "created_at"}

        client.send({"type": "control_response", "request_id": "x", "response": {}})
        err = (await client.recv_until(is_type("error")))[-1]
        assert err["subtype"] == "unsupported" and "WebSocket-only" in err["message"]
        client.send({"type": "ping", "tick": 7})
        assert (await client.recv_until(is_type("pong")))[-1]["tick"] == 7

        level = await wait_for_event(
            http, lambda e: e["type"] == "rtc.audio_level" and e["data"]["rtc_id"] == rtc_id
        )
        assert level["aggregate_id"] == "server" and level["data"]["speaking"] is True
        assert level["data"]["rms"] > 0.1

        peers = (await http.get("/api/rtc")).json()
        assert [p["rtc_id"] for p in peers] == [rtc_id] and peers[0]["state"] == "connected"
        assert (await http.delete(f"/api/rtc/{rtc_id}")).json() == {"closed": True}
        assert (await http.get("/api/rtc")).json() == []
        assert (await http.delete(f"/api/rtc/{rtc_id}")).json() == {"closed": False}
        closed = await wait_for_event(http, lambda e: e["type"] == "rtc.closed")
        assert closed["data"] == {"rtc_id": rtc_id, "session_id": None, "reason": "client"}
    finally:
        await client.close()


async def test_offer_with_session_autosubscribes(http: httpx.AsyncClient, services: Services) -> None:
    pytest.importorskip("aiortc")
    sess = await services.create_session(title="auto")
    client = Client(audio=False)
    try:
        await client.connect(http, session_id=sess.id)
        await client.recv_until(is_type("connected"))
        await http.post("/api/chat", json={"session_id": sess.id, "message": "auto"})
        frames = await client.recv_until(is_type("run.completed"))
        assert frames[-1]["data"]["text"] == "echo: auto"
        assert not any(f.get("type") == "session.created" for f in frames)  # live only
    finally:
        await client.close()


async def test_audit_rows_carry_no_sdp(http: httpx.AsyncClient, services: Services) -> None:
    pytest.importorskip("aiortc")
    client = Client(audio=False)
    try:
        body = await client.connect(http)
        await http.delete(f"/api/rtc/{body['rtc_id']}")
    finally:
        await client.close()
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    async with services.db.session() as s:
        rows = list((await s.execute(select(AuditRow))).scalars())
    actions = {r.action: r for r in rows}
    assert {"rtc.offer", "rtc.close"} <= set(actions)
    blob = json.dumps([r.detail for r in rows])
    assert "v=0" not in blob and "a=fingerprint" not in blob
    assert actions["rtc.offer"].detail["sdp_bytes"] > 100


# ------------------------------------------------------------------------ limits
async def test_oversized_sdp_is_413(http: httpx.AsyncClient) -> None:
    r = await http.post("/api/rtc/offer", json={"sdp": "a" * (64 * 1024 + 1), "type": "offer"})
    assert r.status_code == 413


async def test_unknown_session_is_404_and_garbage_sdp_is_400(http: httpx.AsyncClient) -> None:
    pytest.importorskip("aiortc")
    r = await http.post("/api/rtc/offer", json={"sdp": "v=0", "session_id": "ses_nope"})
    assert r.status_code == 404
    r = await http.post("/api/rtc/offer", json={"sdp": "not an sdp"})
    assert r.status_code == 400 and "not an sdp" not in r.text
    assert (await http.get("/api/rtc")).json() == []


async def test_peer_cap_is_429(http: httpx.AsyncClient, settings: Settings) -> None:
    pytest.importorskip("aiortc")
    settings.rtc_max_peers = 1
    first, second = Client(audio=False), Client(audio=False)
    try:
        await first.connect(http)
        await second.pc.setLocalDescription(await second.pc.createOffer())
        r = await http.post("/api/rtc/offer", json={"sdp": second.pc.localDescription.sdp, "type": "offer"})
        assert r.status_code == 429
    finally:
        await first.close()
        await second.close()


async def test_offer_requires_auth(services: Services, settings: Settings) -> None:
    settings.server_password = "s3cret-pass"
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base) as c:
        assert (await c.post("/api/rtc/offer", json={"sdp": "v=0"})).status_code == 401
        assert (await c.get("/api/rtc")).status_code == 401
        ok = await c.get("/api/rtc", headers={"Authorization": "Bearer s3cret-pass"})
        assert ok.status_code == 200


async def test_offer_without_aiortc_is_501(http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "aiortc", None)  # import aiortc → ImportError
    r = await http.post("/api/rtc/offer", json={"sdp": "v=0", "type": "offer"})
    assert r.status_code == 501 and r.json() == {"detail": "install auoreo[rtc]"}
