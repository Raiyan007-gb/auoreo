"""I8 wake daemon: O_EXCL single instance, fake microphone, detection → routing → one utterance
submitted, pause during a running turn, dead-mic status, the sidecar `wake` method."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends.wake import WakeHit
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.core.settings_i8 import WakeRoute
from auoreo.gateway.fake import FakeGateway
from auoreo.voice import wake as wake_mod
from auoreo.voice.audio import read_wav, rms
from auoreo.voice.wake import WakeDaemon, WakeLocked, acquire_lock, release_lock
from tests.audio import tone_pcm, tone_wav

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake", "audio_input": "fake"}
SILENCE_S = b"\0\0" * 16000


def test_lock_is_exclusive_and_recovers_stale(tmp_path: Path) -> None:
    path = tmp_path / "wake.lock"
    acquire_lock(path)
    with pytest.raises(WakeLocked):
        acquire_lock(path)
    release_lock(path)
    assert not path.exists()
    path.write_text(json.dumps({"pid": 2**22 + 12345, "ts": 0}))  # a dead pid
    acquire_lock(path)
    assert json.loads(path.read_text())["pid"] == os.getpid()
    release_lock(path)


class LoudWake:
    """Detects the phrase whenever the window has a loud stretch (stands in for a KWS model)."""

    def __init__(self, phrase: str = "hey oreo") -> None:
        self.phrase = phrase
        self.calls = 0

    async def detect(self, wav: bytes) -> WakeHit | None:
        self.calls += 1
        pcm = read_wav(wav).pcm
        return WakeHit(self.phrase, 0.0, 0.9) if rms(pcm[-16000:]) > 0.1 else None


@pytest.fixture
async def svc(home: Path, workdir: Path, gateway: FakeGateway) -> Any:
    st = Settings(home=home, gateway="fake", backends=dict(FAKES), voice_endpoint_silence_ms=600)
    st.i8.wake.routes = {"hey oreo": WakeRoute(title="kitchen")}
    s = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield s
    finally:
        await s.close()


async def _events(svc: Services, kind: str) -> list[Any]:
    return [e for e in await svc.events.replay(0, None) if e.type == kind]


async def test_wake_hit_routes_one_utterance_then_listens_again(svc: Services) -> None:
    det = LoudWake()
    svc._backends["wake"] = det  # type: ignore[attr-defined]
    daemon = WakeDaemon(svc)
    assert await daemon.start() and daemon.status == "listening"
    second = WakeDaemon(svc)
    assert not await second.start() and second.status == "locked"  # single instance
    mic = daemon.audio
    mic.feed(SILENCE_S)  # quiet room: detection runs, nothing found
    mic.feed(tone_pcm(0.6))  # "hey oreo"
    await asyncio.sleep(0.3)
    await daemon.drain()
    assert daemon.hits == 1 and daemon.status == "capturing"
    (hit,) = await _events(svc, "voice.wake")
    sid = hit.data["session_id"]
    assert hit.data["phrase"] == "hey oreo" and sid
    mic.feed(SILENCE_S[:9600] + tone_pcm(0.8) + SILENCE_S)  # the command, then silence
    for _ in range(100):
        await asyncio.sleep(0.02)
        if await _events(svc, "voice.transcript"):
            break
    await daemon.drain()
    (tr,) = await _events(svc, "voice.transcript")
    assert tr.data["source"] == "wake" and tr.data["session_id"] == sid
    assert tr.data["text"].startswith("fake transcript (")
    assert daemon.status in ("listening", "paused")
    sess = await svc.get_session(sid)
    assert sess is not None and sess.title == "kitchen"
    await daemon.close()
    await second.close()
    assert not daemon.lock_path.exists()


async def test_wake_pauses_while_the_routed_turn_runs(svc: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    det = LoudWake()
    svc._backends["wake"] = det  # type: ignore[attr-defined]
    daemon = WakeDaemon(svc)
    sess = await svc.create_session(title="x")
    daemon._sessions["hey oreo"] = sess.id
    daemon.status = "listening"
    monkeypatch.setattr(svc.coordinator, "is_running", lambda sid: sid == sess.id)
    for _ in range(20):
        await daemon.on_chunk(tone_pcm(0.1))
    await daemon.drain()
    assert det.calls == 0 and daemon.status == "paused"
    monkeypatch.setattr(svc.coordinator, "is_running", lambda sid: False)
    daemon.pause()
    await daemon.on_chunk(tone_pcm(0.6))
    assert det.calls == 0
    daemon.resume()
    await daemon.on_chunk(tone_pcm(0.6))
    await daemon.drain()
    assert det.calls == 1 and daemon.hits == 1


async def test_dead_mic_status_and_recovery(svc: Services) -> None:
    daemon = WakeDaemon(svc)
    daemon.cfg.dead_mic_window_s = 1.0
    svc._backends["wake"] = LoudWake()  # type: ignore[attr-defined]
    daemon.status = "listening"
    for _ in range(12):
        await daemon.on_chunk(b"\x05\x00" * 1600)  # peak 5 ≤ 10 for 1.2 s
    assert daemon.status == "dead_mic"
    await daemon.on_chunk(tone_pcm(0.1, amp=0.01))
    assert daemon.status == "listening"
    statuses = [e.data["status"] for e in await _events(svc, "voice.wake_status")]
    assert "dead_mic" in statuses and statuses[-1] == "listening"


async def test_wake_drops_a_hallucinated_command(svc: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = WakeDaemon(svc)
    sess = await svc.create_session(title="x")
    daemon._sessions["hey oreo"] = sess.id
    voice = svc.ext["voice"]

    async def fake_stt(_wav: bytes, **_: Any) -> Any:
        from auoreo.backends.stt import Transcript

        return Transcript("Thank you for watching.")

    monkeypatch.setattr(voice, "transcribe_wav", fake_stt)
    await daemon._utterance("hey oreo", tone_pcm(0.5))
    (drop,) = await _events(svc, "voice.transcript_dropped")
    assert drop.data["reason"] == "blocklist" and not await _events(svc, "voice.transcript")


async def test_i8_starter_runs_wake_only_when_enabled(
    home: Path, workdir: Path, gateway: FakeGateway
) -> None:
    st = Settings(home=home, gateway="fake", backends=dict(FAKES))
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        rt = svc.ext["i8_boot"]
        assert rt.wake is None and rt.cu_watch is not None and rt.livekit is None
    finally:
        await svc.close()
    st2 = Settings(home=home, gateway="fake", backends=dict(FAKES))
    st2.i8.wake.enabled = True
    svc2 = await Services.create(st2, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        rt = svc2.ext["i8_boot"]
        assert rt.wake is not None and rt.wake.status == "listening"
        assert (home / "wake.lock").exists()
    finally:
        await svc2.close()
    assert not (home / "wake.lock").exists()


async def test_host_wake_method_and_sidecar_backend(home: Path) -> None:
    from auoreo.voice.host import VoiceHostServer, host_names

    st = Settings(home=home, gateway="fake")
    assert host_names(st)["wake"] == "fake"
    server = VoiceHostServer(st, host_names(st))
    import base64

    hit = await server.handle(
        {
            "id": 1,
            "method": "wake",
            "params": {"wav_b64": base64.b64encode(tone_wav(0.2, comment="hey oreo please")).decode()},
        }
    )
    assert hit is not None and hit["result"]["hit"]["phrase"] == "hey oreo"
    miss = await server.handle(
        {
            "id": 2,
            "method": "wake",
            "params": {"wav_b64": base64.b64encode(tone_wav(0.2, comment="hello")).decode()},
        }
    )
    assert miss is not None and miss["result"] == {"hit": None}
    st.i8.wake.host_backend = "sidecar"
    bad = VoiceHostServer(st, host_names(st))
    err = await bad.handle({"id": 3, "method": "wake", "params": {"wav_b64": ""}})
    assert err is not None and "recurse" in err["error"]["message"]


async def test_wake_sidecar_backend_through_real_host(home: Path) -> None:
    from auoreo.harness.discovery import Root
    from auoreo.harness.registry import Harness
    from auoreo.voice.sidecar import close_all
    from tests.conftest import REPO

    st = Settings(home=home, gateway="fake", voice_host_timeout_s=60)
    h = Harness([Root("bundled", REPO)])
    backend = h.snapshot.backends["wake/sidecar"].load()(st)
    try:
        assert (await backend.check()).ok
        hit = await backend.detect(tone_wav(0.2, comment="Hey Oreo, lights on"))
        assert hit is not None and hit.phrase == "hey oreo"
        assert await backend.detect(tone_wav(0.2, comment="nothing")) is None
    finally:
        await close_all()


def test_module_docs_mention_contract() -> None:
    assert "O_EXCL" in (wake_mod.__doc__ or "") and "dead_mic" in (wake_mod.__doc__ or "")
