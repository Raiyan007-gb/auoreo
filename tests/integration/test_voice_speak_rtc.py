"""The agent speaks back over WebRTC (F1): aiortc loopback against a real uvicorn server.

An assistant `message.completed` for a session with an attached peer is synthesized (fake TTS
= 440 Hz tone) onto the peer's outbound track; the client measures the energy it receives.
Also: per-peer opt-out, truncation, and the echo guard (the client mirrors what it hears
back to the server, which must not transcribe its own voice)."""

from __future__ import annotations

import asyncio
import contextlib
import fractions
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from tests.integration.test_api import parse_sse
from tests.integration.test_rtc import Client, running_server, wait_for_event

pytest.importorskip("aiortc")

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake"}


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, backends=dict(FAKES), rtc_ice_servers=[])


@pytest.fixture
async def http(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        yield c


class Listener:
    """Client side: records the RMS of every received 20 ms audio frame."""

    def __init__(self, client: Client) -> None:
        self.levels: list[float] = []
        self.track: Any = None
        self._task: asyncio.Task[None] | None = None
        client.pc.on("track", self._on_track)

    def _on_track(self, track: Any) -> None:
        if track.kind == "audio":
            self.track = track
            self._task = asyncio.get_running_loop().create_task(self._pump(track))

    async def _pump(self, track: Any) -> None:
        import numpy as np
        from aiortc.mediastreams import MediaStreamError

        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            x = frame.to_ndarray().astype("float64").reshape(-1) / 32768.0
            self.levels.append(float(np.sqrt(np.mean(x * x))))

    def loud_frames(self, threshold: float = 0.05) -> int:
        return sum(1 for v in self.levels if v > threshold)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


def listening_client() -> tuple[Client, Listener]:
    client = Client(audio=False)
    client.pc.addTransceiver("audio", direction="recvonly")
    return client, Listener(client)


async def events_of(http: httpx.AsyncClient, kind: str) -> list[dict[str, Any]]:
    r = await http.get("/api/events", params={"follow": "false"})
    return [e for e in parse_sse(r.text) if e["type"] == kind]


async def test_completed_reply_is_spoken_to_the_client(http: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session(title="speak")
    client, listener = listening_client()
    try:
        answer = await client.connect(http, session_id=sess.id)
        rtc_id = answer["rtc_id"]
        assert services.rtc is not None and services.rtc.peers[rtc_id].speech is not None
        r = await http.post("/api/chat", json={"session_id": sess.id, "message": "say something nice"})
        assert r.status_code == 202
        speaking = await wait_for_event(http, lambda e: e["type"] == "voice.speaking", timeout=10)
        assert speaking["aggregate_id"] == sess.id
        assert speaking["data"]["rtc_id"] == rtc_id and speaking["data"]["truncated"] is False
        spoke = await wait_for_event(http, lambda e: e["type"] == "voice.spoke", timeout=10)
        # "echo: say something nice." ≈ 25 chars / 15 cps ≈ 1.7 s of tone
        assert spoke["data"]["duration_s"] > 1.0 and spoke["data"]["cancelled"] is False
        assert spoke["data"]["backend"] == "fake"
        async with asyncio.timeout(10):
            while listener.loud_frames() < 40:  # ≥ 0.8 s of audible speech arrived
                await asyncio.sleep(0.1)
        assert max(listener.levels) > 0.15  # the 0.3-amplitude tone survives Opus
        assert not (await events_of(http, "voice.transcript"))  # recvonly: nothing to transcribe
    finally:
        await listener.stop()
        await client.close()


async def test_speak_opt_out_per_peer_and_globally(
    http: httpx.AsyncClient, services: Services, settings: Settings
) -> None:
    sess = await services.create_session(title="quiet")
    client, _ = listening_client()
    try:
        answer = await client.connect(http, session_id=sess.id, speak=False)
        assert services.rtc is not None and services.rtc.peers[answer["rtc_id"]].speech is None
        await http.post("/api/chat", json={"session_id": sess.id, "message": "hello"})
        await wait_for_event(http, lambda e: e["type"] == "run.completed", timeout=10)
        await asyncio.sleep(0.3)
        assert not await events_of(http, "voice.speaking")
    finally:
        await client.close()
    settings.voice_speak_over_rtc = False
    client2, _ = listening_client()
    try:
        answer = await client2.connect(http, session_id=sess.id)
        assert services.rtc.peers[answer["rtc_id"]].speech is None
    finally:
        await client2.close()


async def test_long_reply_is_truncated(
    http: httpx.AsyncClient, services: Services, settings: Settings
) -> None:
    settings.voice_speak_max_chars = 60
    sess = await services.create_session(title="long")
    client, _ = listening_client()
    long = "First sentence is here. " + "This part goes on and on without any end in sight " * 4
    try:
        await client.connect(http, session_id=sess.id)
        await http.post("/api/chat", json={"session_id": sess.id, "message": long})
        speaking = await wait_for_event(http, lambda e: e["type"] == "voice.speaking", timeout=10)
        assert speaking["data"]["truncated"] is True and speaking["data"]["chars"] <= 61
        await wait_for_event(http, lambda e: e["type"] == "voice.spoke", timeout=10)
        spoken = "".join(str(c["text"]) for c in services.backend("tts").calls)
        assert spoken.endswith("…") and len(spoken) <= 61
        done = await wait_for_event(http, lambda e: e["type"] == "run.completed", timeout=10)
        assert done["data"]["text"] == f"echo: {long.strip()}"  # the full answer stays text
    finally:
        await client.close()


def mirror_track() -> Any:
    """Client outbound audio that replays whatever the client hears (a delayed copy)."""
    from aiortc import AudioStreamTrack
    from av import AudioFrame

    class Mirror(AudioStreamTrack):
        def __init__(self) -> None:
            super().__init__()
            self.source: Any = None
            self._sent = 0
            self._origin: float | None = None

        async def recv(self) -> Any:
            if self.source is not None:
                frame = await self.source.recv()
            else:
                if self._origin is None:
                    self._origin = time.time()
                await asyncio.sleep(max(0.0, self._origin + self._sent / 48_000 - time.time()))
                frame = AudioFrame(format="s16", layout="mono", samples=960)
                frame.planes[0].update(bytes(1920))
                frame.sample_rate = 48_000
            frame.pts = self._sent
            frame.time_base = fractions.Fraction(1, 48_000)
            self._sent += frame.samples
            return frame

    return Mirror()


async def test_echo_of_own_voice_is_not_transcribed(http: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session(title="echo")
    client = Client(audio=False)
    mirror = mirror_track()
    client.pc.addTrack(mirror)
    client.pc.on("track", lambda t: setattr(mirror, "source", t) if t.kind == "audio" else None)
    try:
        answer = await client.connect(http, session_id=sess.id)
        rtc_id = answer["rtc_id"]
        await http.post("/api/chat", json={"session_id": sess.id, "message": "repeat after me please"})
        spoke = await wait_for_event(http, lambda e: e["type"] == "voice.spoke", timeout=10)
        await asyncio.sleep(spoke["data"]["duration_s"] + 2.0)  # playback + echo tail + endpoint
        voice = services.ext["voice"]
        assert voice.bridge.peers[rtc_id].echo_dropped > 20  # the echo arrived and was dropped
        assert not await events_of(http, "voice.transcript")
        assert not await events_of(http, "voice.barge_in")
    finally:
        await client.close()
