"""I8: the same voice loopback contract on BOTH transports (spec §26.6) — aiortc P2P (real
loopback through the server) and LiveKit (the `LiveKitLink` peer driven through a fake
`livekit.rtc` namespace, plus the `livekit-agents` worker entrypoint with fake `agents`).

Contract: speech in → `voice.transcript` (+ followup turn) → the reply is spoken back (loud
outbound frames, `voice.speaking`/`voice.spoke`) → barge-in (`response.cancel`) cuts the
speech (`voice.speech_cancelled`; LiveKit also `AgentSession.interrupt`) → bounds (SDP > 64 KiB
→ 413 on aiortc, inbound frame > 128 KiB dropped on LiveKit).

Also here: streaming STT partials, the echo/hallucination filter and the noise floor on the
RTC bridge.
"""

from __future__ import annotations

import asyncio
import math
from array import array
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.rtc.peers import manager_for
from auoreo.voice.livekit_link import MAX_FRAME_BYTES, LiveKitLink
from auoreo.voice.livekit_worker import LiveKitWorker, tap_stt
from auoreo.voice.rtc_bridge import RtcVoiceBridge
from tests.audio import tone_pcm

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake"}
SILENCE_1S = b"\0\0" * 16000


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, backends=dict(FAKES), rtc_ice_servers=[])


async def wait_event(svc: Services, pred: Callable[[Any], bool], timeout: float = 15) -> Any:
    async with asyncio.timeout(timeout):
        while True:
            hits = [e for e in await svc.events.replay(0, None) if pred(e)]
            if hits:
                return hits[0]
            await asyncio.sleep(0.05)


def rms16(pcm: bytes) -> float:
    a = array("h")
    a.frombytes(pcm)
    return math.sqrt(sum(v * v for v in a) / max(1, len(a))) / 32768.0


# --------------------------------------------------------------- fake livekit
class FakeAudioFrame:
    def __init__(self, data: bytes, sample_rate: int, num_channels: int, samples_per_channel: int) -> None:
        self.data, self.sample_rate = memoryview(bytes(data)), sample_rate
        self.num_channels, self.samples_per_channel = num_channels, samples_per_channel


class FakeAudioSource:
    def __init__(self, sample_rate: int, num_channels: int) -> None:
        self.sample_rate, self.frames = sample_rate, []

    async def capture_frame(self, frame: FakeAudioFrame) -> None:
        self.frames.append(bytes(frame.data))

    def loud(self, threshold: float = 0.05) -> int:
        return sum(1 for f in self.frames if rms16(f) > threshold)


class FakeLocalAudioTrack:
    @staticmethod
    def create_audio_track(name: str, source: FakeAudioSource) -> Any:
        return SimpleNamespace(name=name, source=source)


FAKE_RTC = SimpleNamespace(
    AudioSource=FakeAudioSource,
    AudioFrame=FakeAudioFrame,
    LocalAudioTrack=FakeLocalAudioTrack,
    TrackPublishOptions=lambda source: SimpleNamespace(source=source),
    TrackSource=SimpleNamespace(SOURCE_MICROPHONE=2),
)


# ------------------------------------------------------------------ transports
class LoopbackTransport:
    name = ""

    async def open(self, svc: Services, session_id: str) -> str: ...

    async def send_speech(self) -> None: ...

    def received_loud(self) -> int: ...

    async def close(self) -> None: ...


class AiortcTransport(LoopbackTransport):
    name = "aiortc"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def open(self, svc: Services, session_id: str) -> str:
        from tests.integration.test_rtc import Client, running_server
        from tests.integration.test_voice_rtc import gappy_track
        from tests.integration.test_voice_speak_rtc import Listener

        self._server = running_server(svc, self.settings)
        base = await self._server.__aenter__()
        self.http = httpx.AsyncClient(base_url=base, timeout=10)
        self.client = Client(audio=False)
        self.client.pc.addTrack(gappy_track(0.8, 3.0))
        self.listener = Listener(self.client)
        answer = await self.client.connect(self.http, session_id=session_id)
        return str(answer["rtc_id"])

    async def send_speech(self) -> None:
        return None  # the gappy track keeps sending

    def received_loud(self) -> int:
        return self.listener.loud_frames()

    async def oversized(self) -> int:
        r = await self.http.post(
            "/api/rtc/offer", json={"sdp": "v=0\r\n" + "a" * (65 * 1024), "type": "offer"}
        )
        return r.status_code

    async def close(self) -> None:
        await self.listener.stop()
        await self.client.close()
        await self.http.aclose()
        await self._server.__aexit__(None, None, None)


class LiveKitTransport(LoopbackTransport):
    name = "livekit"

    async def open(self, svc: Services, session_id: str) -> str:
        self.interrupts = 0
        self.published: list[Any] = []

        async def publish(track: Any) -> None:
            self.published.append(track)

        def interrupt() -> None:
            self.interrupts += 1

        mgr = manager_for(svc)
        self.link = LiveKitLink(
            svc, mgr, room="room-1", session_id=session_id, rtc=FAKE_RTC, publish=publish, interrupt=interrupt
        )
        await self.link.start()
        return self.link.rtc_id

    async def send_speech(self) -> None:
        pcm = tone_pcm(0.8) + SILENCE_1S * 2
        for i in range(0, len(pcm), 640):  # 20 ms chunks at 16 kHz
            self.link.feed(pcm[i : i + 640])
            await asyncio.sleep(0)

    def received_loud(self) -> int:
        return int(self.link.source.loud())

    async def oversized(self) -> int:
        before = self.link.dropped_frames
        self.link.feed(b"\x01\x00" * (MAX_FRAME_BYTES // 2 + 1))
        return 413 if self.link.dropped_frames == before + 1 else 200

    async def close(self) -> None:
        await self.link.close()


@pytest.fixture(params=["aiortc", "livekit"])
async def transport(request: pytest.FixtureRequest, settings: Settings) -> AsyncIterator[LoopbackTransport]:
    if request.param == "aiortc":
        pytest.importorskip("aiortc")
        t: LoopbackTransport = AiortcTransport(settings)
    else:
        t = LiveKitTransport()
    yield t


async def test_voice_loopback_contract_on_both_transports(
    transport: LoopbackTransport, services: Services
) -> None:
    sess = await services.create_session(title=f"loop-{transport.name}")
    rtc_id = await transport.open(services, sess.id)
    try:
        assert rtc_id in manager_for(services).peers
        await transport.send_speech()
        tr = await wait_event(
            services, lambda e: e.type == "voice.transcript" and e.data.get("rtc_id") == rtc_id
        )
        assert tr.data["session_id"] == sess.id and tr.data["text"].startswith("fake transcript (")
        done = await wait_event(services, lambda e: e.type == "run.completed" and e.aggregate_id == sess.id)
        assert done.data["text"].startswith("echo: fake transcript (")
        await wait_event(services, lambda e: e.type == "voice.spoke" and e.data.get("rtc_id") == rtc_id)
        async with asyncio.timeout(10):
            while transport.received_loud() < 25:  # ≥ 0.5 s of the spoken reply arrived
                await asyncio.sleep(0.05)
        # barge-in / response.cancel cuts queued speech
        peer = manager_for(services).peers[rtc_id]
        peer.say(tone_pcm(3.0), 16_000)
        assert peer.speaking
        dropped = peer.stop_speaking("barge_in")
        assert dropped > 0 and not peer.speaking
        await wait_event(
            services, lambda e: e.type == "voice.speech_cancelled" and e.data.get("rtc_id") == rtc_id
        )
        if isinstance(transport, LiveKitTransport):
            assert transport.interrupts == 1 and transport.published[0].name == "auoreo-voice"
        assert await transport.oversized() == 413  # SDP > 64 KiB / frame > 128 KiB
    finally:
        await transport.close()
    assert rtc_id not in manager_for(services).peers


# ------------------------------------------------------- livekit-agents worker
class FakeSession:
    def __init__(self, **kw: Any) -> None:
        self.kw, self.started, self.interrupted = kw, None, 0

    async def start(self, *, agent: Any, room: Any, room_options: Any) -> None:
        self.started = (agent, room, room_options)

    def interrupt(self, *, force: bool = False) -> None:
        self.interrupted += 1


def fake_agents() -> SimpleNamespace:
    """The slice of `livekit.agents` the worker uses (constructors record their arguments)."""

    class RecognizeStream:
        def __init__(self, *, stt: Any, conn_options: Any, sample_rate: int) -> None:
            self.queue: asyncio.Queue[Any] = asyncio.Queue()
            self.sample_rate = sample_rate

        @property
        def _input_ch(self) -> Any:
            async def gen() -> AsyncIterator[Any]:
                while (item := await self.queue.get()) is not None:
                    yield item

            return gen()

    class STT:
        def __init__(self, *, capabilities: Any) -> None:
            self.capabilities = capabilities

    servers: list[Any] = []

    class AgentServer:
        def __init__(self, **kw: Any) -> None:
            self.kw, self.entry, self.ran = kw, None, False
            servers.append(self)

        def rtc_session(self, *, agent_name: str) -> Any:
            self.agent_name = agent_name

            def deco(fn: Any) -> Any:
                self.entry = fn
                return fn

            return deco

        async def run(self, *, devmode: bool, unregistered: bool) -> None:
            self.ran = True
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            return None

    stt = SimpleNamespace(
        RecognizeStream=RecognizeStream,
        STT=STT,
        STTCapabilities=lambda **kw: SimpleNamespace(**kw),
        SpeechEvent=lambda **kw: SimpleNamespace(**kw),
        SpeechEventType=SimpleNamespace(FINAL_TRANSCRIPT="final_transcript"),
    )
    return SimpleNamespace(
        stt=stt,
        AgentServer=AgentServer,
        AgentSession=FakeSession,
        Agent=lambda **kw: SimpleNamespace(**kw),
        TurnHandlingOptions=lambda **kw: dict(kw),
        JobExecutorType=SimpleNamespace(THREAD="thread"),
        DEFAULT_API_CONNECT_OPTIONS=None,
        voice=SimpleNamespace(room_io=SimpleNamespace(RoomOptions=lambda **kw: SimpleNamespace(**kw))),
        servers=servers,
    )


async def test_tap_stt_forwards_resampled_audio() -> None:
    got: list[bytes] = []
    stt = tap_stt(fake_agents(), got.append)
    stream = stt.stream()
    assert stream.sample_rate == 16_000 and stt.capabilities.streaming
    await stream.queue.put(SimpleNamespace(data=memoryview(b"\x01\x00" * 320)))
    await stream.queue.put(None)
    await stream._run()
    assert got == [b"\x01\x00" * 320]


async def test_livekit_worker_entrypoint_bridges_the_room(
    home: Path, workdir: Path, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = Settings(home=home, gateway="fake", backends={**FAKES, "rtc_transport": "livekit"})
    st.livekit.url = "ws://127.0.0.1:7880"
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-secret-secret")
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    agents = fake_agents()
    worker = LiveKitWorker(svc, agents=agents, rtc=FAKE_RTC)
    try:
        assert await worker.start() and worker.status == "running"
        (server,) = agents.servers
        assert server.kw["job_executor_type"] == "thread" and server.kw["ws_url"] == "ws://127.0.0.1:7880"
        assert server.agent_name == "auoreo"
        published: list[Any] = []
        shutdown: list[Any] = []
        room = SimpleNamespace(
            name="r-abc",
            local_participant=SimpleNamespace(publish_track=lambda t, o: _record(published, t)),
        )

        async def connect() -> None:
            return None

        ctx = SimpleNamespace(room=room, connect=connect, add_shutdown_callback=shutdown.append)
        await server.entry(ctx)
        (rtc_id,) = worker.links
        session = worker.sessions[rtc_id]
        agent, used_room, options = session.started
        assert used_room is room and options.audio_output is False and options.text_output is False
        assert session.kw["turn_handling"] == {"turn_detection": "manual"}
        link = worker.links[rtc_id]
        assert link.peer.session_id and await svc.get_session(link.peer.session_id) is not None
        assert published and published[0].name == "auoreo-voice"
        link.peer.say(tone_pcm(1.0), 16_000)
        link.peer.stop_speaking("barge_in")
        assert session.interrupted == 1  # barge-in → AgentSession.interrupt
        await shutdown[0]()
        assert rtc_id not in manager_for(svc).peers
    finally:
        await worker.close()
        await svc.close()


async def _record(bucket: list[Any], track: Any) -> None:
    bucket.append(track)


async def test_livekit_worker_reports_unavailable_honestly(services: Services) -> None:
    worker = LiveKitWorker(services)
    assert not await worker.start()
    assert worker.status == "unavailable" and "livekit" in worker.detail


# -------------------------------------------- bridge: streaming, filter, floor
class _Coordinator:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str, str]] = []

    def is_running(self, session_id: str) -> bool:
        return False

    async def submit(self, session_id: str, text: str, kind: str = "followup") -> None:
        self.submitted.append((session_id, text, kind))


async def _feed(bridge: RtcVoiceBridge, pcm: bytes, sid: str) -> None:
    for i in range(0, len(pcm), 640):
        bridge.on_frames("rtc_s", sid, pcm[i : i + 640], 16_000)
    await asyncio.sleep(0)


async def test_streaming_partials_and_echo_filter(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    st = Settings(home=home, gateway="fake", backends=dict(FAKES), voice_endpoint_silence_ms=600)
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    real = svc.coordinator
    try:
        sess = await svc.create_session(title="s")
        fake = _Coordinator()
        svc.coordinator = fake  # type: ignore[assignment]
        voice = svc.ext["voice"]
        voice.stt.streaming = True
        bridge = RtcVoiceBridge(voice)
        await _feed(bridge, tone_pcm(0.6) + SILENCE_1S, sess.id)
        async with asyncio.timeout(5):
            while not fake.submitted:
                await asyncio.sleep(0.02)
        assert fake.submitted[0][1].startswith("fake transcript (")
        kinds = await svc.events.replay(0, frozenset({f"session:{sess.id}"}))
        partials = [e.data["text"] for e in kinds if e.type == "voice.partial"]
        assert partials[0] == "fake" and partials[-1].startswith("fake transcript")
        # the agent just said nearly the same words → the next utterance is dropped as echo
        voice.echo.add(sess.id, "fake transcript (0.9 s) is what I heard")
        await _feed(bridge, tone_pcm(0.4) + SILENCE_1S, sess.id)
        dropped = None
        async with asyncio.timeout(5):
            while dropped is None:
                evs = await svc.events.replay(0, frozenset({f"session:{sess.id}"}))
                dropped = next((e for e in evs if e.type == "voice.transcript_dropped"), None)
                await asyncio.sleep(0.02)
        assert dropped.data["reason"] == "echo" and len(fake.submitted) == 1
        await bridge.close()
    finally:
        svc.coordinator = real
        await svc.close()


def test_noise_floor_raises_the_barge_bar() -> None:
    from auoreo.voice.barge import NoiseFloor

    nf = NoiseFloor(calibrate_ms=400, multiplier=3.0)
    assert nf.threshold(0.02) == 0.02
    for _ in range(20):
        nf.feed(20, 0.03, quiet=True)  # a noisy room: fan at 0.03 RMS
    assert nf.ready and abs(nf.floor - 0.03) < 1e-9
    assert abs(nf.threshold(0.02) - 0.09) < 1e-9  # the room noise ×3 beats the base threshold
    nf.feed(20, 0.5, quiet=False)  # speech never moves the floor
    assert abs(nf.floor - 0.03) < 1e-9
