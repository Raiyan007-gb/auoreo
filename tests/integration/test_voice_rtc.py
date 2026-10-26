"""Live transcription over WebRTC (M4c): aiortc loopback with a gappy sine track → fake VAD
endpointing → fake STT → `voice.transcript` events and a followup turn. The bridge state
machine (barge-in, steer while running) is also unit-tested without aiortc."""

from __future__ import annotations

import asyncio
import fractions
import math
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.voice.rtc_bridge import RtcVoiceBridge
from tests.audio import tone_pcm
from tests.integration.test_rtc import Client, running_server, wait_for_event

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake"}


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(
        home=home,
        gateway="fake",
        approval_timeout_s=5,
        backends=dict(FAKES),
        rtc_ice_servers=[],
        voice_endpoint_silence_ms=700,  # the 1 s gaps of `gappy_track` end utterances
    )


@pytest.fixture
async def http(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        yield c


def gappy_track(on_s: float = 0.8, off_s: float = 1.0) -> Any:
    from aiortc import AudioStreamTrack
    from av import AudioFrame

    class GappySine(AudioStreamTrack):
        """20 ms frames at 48 kHz: `on_s` of 440 Hz tone, then `off_s` of silence, repeating."""

        rate, samples = 48_000, 960

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
            if (self._t / self.rate) % (on_s + off_s) >= on_s:
                pcm[:] = 0
            frame = AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = self.rate
            frame.pts = self._t
            frame.time_base = fractions.Fraction(1, self.rate)
            self._t += self.samples
            return frame

    return GappySine()


async def test_rtc_audio_produces_transcripts_and_a_followup_turn(
    http: httpx.AsyncClient, services: Services
) -> None:
    pytest.importorskip("aiortc")
    sess = await services.create_session(title="live")
    client = Client(audio=False)
    client.pc.addTrack(gappy_track())
    try:
        answer = await client.connect(http, session_id=sess.id)
        rtc_id = answer["rtc_id"]
        assert services.rtc is not None and services.rtc.on_audio_frames is not None  # attached
        transcript = await wait_for_event(
            http, lambda e: e["type"] == "voice.transcript" and e["data"]["rtc_id"] == rtc_id, timeout=15
        )
        assert transcript["aggregate_id"] == sess.id
        data = transcript["data"]
        assert data["session_id"] == sess.id and data["text"].startswith("fake transcript (")
        # ≈ 0.8 s tone + pre-roll + 0.2 s tail per utterance
        assert 0.8 <= data["duration_s"] <= 2.2, data
        done = await wait_for_event(
            http,
            lambda e: e["type"] == "run.completed" and e["aggregate_id"] == sess.id,
            timeout=15,
        )
        assert done["data"]["text"].startswith("echo: fake transcript (")
    finally:
        await client.close()


# ------------------------------------------------------- bridge state machine
class _Coordinator:
    def __init__(self) -> None:
        self.running = False
        self.submitted: list[tuple[str, str, str]] = []

    def is_running(self, session_id: str) -> bool:
        return self.running

    async def submit(self, session_id: str, text: str, kind: str = "followup") -> str | None:
        self.submitted.append((session_id, text, kind))
        return None


async def _feed(bridge: RtcVoiceBridge, pcm: bytes, session_id: str | None, chunk: int = 640) -> None:
    import numpy as np

    for i in range(0, len(pcm), chunk):
        bridge.on_frames("rtc_x", session_id, np.frombuffer(pcm[i : i + chunk], dtype="<i2"), 16_000)
    await asyncio.sleep(0)


async def test_bridge_barge_in_and_steer(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    pytest.importorskip("numpy")
    st = Settings(home=home, gateway="fake", backends=dict(FAKES), voice_endpoint_silence_ms=700)
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    real = svc.coordinator
    try:
        sess = await svc.create_session(title="b")
        fake = _Coordinator()
        svc.coordinator = fake  # type: ignore[assignment]
        bridge = RtcVoiceBridge(svc.ext["voice"])
        fake.running = True
        await _feed(bridge, b"\0\0" * 8000 + tone_pcm(0.6) + b"\0\0" * 16000, sess.id)
        for _ in range(50):
            if fake.submitted:
                break
            await asyncio.sleep(0.02)
        assert fake.submitted and fake.submitted[0][2] == "steer"
        assert fake.submitted[0][1].startswith("fake transcript (1.0")  # 0.5 s + 0.3 s pre-roll + 0.2 s tail
        fake.running = False
        await _feed(bridge, tone_pcm(0.3) + b"\0\0" * 16000, sess.id)
        for _ in range(50):
            if len(fake.submitted) > 1:
                break
            await asyncio.sleep(0.02)
        assert fake.submitted[1][2] == "followup"
        await _feed(bridge, tone_pcm(0.05) + b"\0\0" * 16000, sess.id)  # < 100 ms: not speech
        await asyncio.sleep(0.1)
        assert len(fake.submitted) == 2
        svc.coordinator = real
        kinds = [e.type for e in await svc.events.replay(0, frozenset({f"session:{sess.id}"}))]
        assert kinds.count("voice.barge_in") == 1 and kinds.count("voice.transcript") == 2
        await bridge.close()
    finally:
        svc.coordinator = real
        await svc.close()
