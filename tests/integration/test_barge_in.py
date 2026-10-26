"""Barge-in that actually cancels (F1, Hermes `_BargeDetector` thresholds).

The bridge is driven directly with 20 ms chunks against real `Services` and a real `Peer`
whose `SpeechQueue` stands in for the outbound track (no aiortc needed): speech during the
agent's speech cuts the output; speech inside the 500 ms grace window does not; the
"interrupted" note reaches the next user text exactly once and expires after its TTL; the
running turn is cancelled only with `voice_barge_in_interrupts_turn`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.rtc.echo import EchoGuard
from auoreo.rtc.outbound import SpeechQueue
from auoreo.rtc.peers import Peer
from auoreo.voice import rtc_bridge
from auoreo.voice.rtc_bridge import SPEECH_INTERRUPTED_NOTE, RtcVoiceBridge
from tests.audio import tone_pcm

pytest.importorskip("numpy")

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake"}
CHUNK = 640  # 20 ms at 16 kHz


class _Manager:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.peers: dict[str, Peer] = {}
        self.on_audio_frames: Any = None
        self._bg: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Any) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)


class _Coordinator:
    """Delegates to the real coordinator but scripts `is_running` and records calls."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.running = True
        self.submitted: list[tuple[str, str, str]] = []
        self.cancelled: list[str] = []

    def is_running(self, session_id: str) -> bool:
        return self.running

    async def submit(self, session_id: str, text: str, kind: str = "followup") -> None:
        self.submitted.append((session_id, text, kind))

    async def cancel(self, session_id: str) -> bool:
        self.cancelled.append(session_id)
        self.running = False
        return True

    async def runtime(self, session_id: str) -> Any:
        return await self.real.runtime(session_id)


@pytest.fixture
async def rig(home: Path, workdir: Path, gateway: FakeGateway) -> AsyncIterator[dict[str, Any]]:
    st = Settings(home=home, gateway="fake", backends=dict(FAKES), voice_endpoint_silence_ms=700)
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    real = svc.coordinator
    sess = await svc.create_session(title="barge")
    coord = _Coordinator(real)
    svc.coordinator = coord  # type: ignore[assignment]
    mgr = _Manager(svc)
    echo = EchoGuard()
    peer = Peer(rtc_id="rtc_b", session_id=sess.id, pc=None, manager=mgr, echo=echo, speech=SpeechQueue(echo))
    mgr.peers[peer.rtc_id] = peer
    bridge = RtcVoiceBridge(svc.ext["voice"])
    bridge.attach(mgr)
    try:
        yield {"svc": svc, "sess": sess, "coord": coord, "peer": peer, "bridge": bridge, "real": real}
    finally:
        await bridge.close()
        svc.coordinator = real
        await svc.close()


def user_voice(seconds: float) -> bytes:
    return tone_pcm(seconds, freq=1000.0, amp=0.5)  # not correlated with the agent's 440 Hz


def feed(bridge: RtcVoiceBridge, pcm: bytes, sid: str, peer: Peer | None = None) -> None:
    import numpy as np

    for i in range(0, len(pcm), CHUNK):
        if peer is not None and peer.speech is not None:
            peer.speech.next_frame()  # the outbound track plays in lockstep
        bridge.on_frames("rtc_b", sid, np.frombuffer(pcm[i : i + CHUNK], dtype="<i2"), 16_000)


async def until(pred: Any, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.02)


async def kinds(svc: Services, sid: str) -> list[str]:
    return [e.type for e in await svc.events.replay(0, frozenset({f"session:{sid}"}))]


async def test_speech_while_speaking_cancels_output_and_steers(rig: dict[str, Any]) -> None:
    svc, sess, coord, peer, bridge = rig["svc"], rig["sess"], rig["coord"], rig["peer"], rig["bridge"]
    peer.say(tone_pcm(5.0, amp=0.3))
    peer.speech.started_at = time.monotonic() - 1.0  # past the 500 ms grace
    feed(bridge, user_voice(0.3), sess.id, peer)
    assert not peer.speaking  # cut after ≥ 80 % of a 300 ms window
    feed(bridge, user_voice(0.4) + bytes(16_000 * 2), sess.id)
    await until(lambda: bool(coord.submitted))
    _, text, kind = coord.submitted[0]
    assert kind == "steer" and coord.cancelled == []  # default: the turn keeps running
    # 1200 ms of pre-roll is kept: the 300 ms that tripped + 0.4 s + 0.2 s tail (≥ 0.9 s)
    secs = float(text.split("(")[1].split("s")[0])
    assert secs >= 0.9, text
    assert text.endswith(SPEECH_INTERRUPTED_NOTE)
    k = await kinds(svc, sess.id)
    assert "voice.speech_cancelled" in k
    barge = [
        e for e in await svc.events.replay(0, frozenset({f"session:{sess.id}"})) if e.type == "voice.barge_in"
    ]
    assert (
        len(barge) == 1 and barge[0].data["cancelled"] is True and barge[0].data["interrupted_turn"] is False
    )


async def test_grace_window_ignores_early_speech(rig: dict[str, Any]) -> None:
    svc, sess, peer, bridge = rig["svc"], rig["sess"], rig["peer"], rig["bridge"]
    peer.say(tone_pcm(5.0, amp=0.3))  # started just now: inside the grace window
    feed(bridge, user_voice(0.4), sess.id, peer)
    assert peer.speaking
    await asyncio.sleep(0.05)
    assert "voice.barge_in" not in await kinds(svc, sess.id)
    peer.speech.started_at = time.monotonic() - 0.6
    feed(bridge, user_voice(0.3), sess.id, peer)
    assert not peer.speaking  # the same speech trips once the grace has passed


async def test_quiet_or_echo_audio_does_not_barge(rig: dict[str, Any]) -> None:
    sess, peer, bridge = rig["sess"], rig["peer"], rig["bridge"]
    agent = tone_pcm(5.0, amp=0.3)
    peer.say(agent)
    peer.speech.started_at = time.monotonic() - 1.0
    feed(bridge, tone_pcm(0.5, freq=1000.0, amp=0.03), sess.id, peer)  # above 0.02, below ×2
    assert peer.speaking
    import numpy as np

    for i in range(25):  # the agent's own voice, 60 ms late
        peer.speech.next_frame()
        start = max(0, (i - 3) * CHUNK)
        bridge.on_frames("rtc_b", sess.id, np.frombuffer(agent[start : start + CHUNK], dtype="<i2"), 16_000)
    assert peer.speaking and bridge.peers["rtc_b"].echo_dropped >= 20


async def test_note_is_delivered_once_and_expires(
    rig: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sess, coord, peer, bridge, real = rig["sess"], rig["coord"], rig["peer"], rig["bridge"], rig["real"]
    rt = await real.runtime(sess.id)
    await bridge._barge_in(peer, "rtc_b", sess.id)
    assert rt.state.pending_user_messages == [SPEECH_INTERRUPTED_NOTE]
    await bridge._barge_in(peer, "rtc_b", sess.id)  # a second barge does not duplicate it
    assert rt.state.pending_user_messages == [SPEECH_INTERRUPTED_NOTE]
    assert bridge.take_note(sess.id) is True and rt.state.pending_user_messages == []
    assert bridge.take_note(sess.id) is False  # once
    # delivered by the runner after a tool round (list flushed): not appended again
    await bridge._barge_in(peer, "rtc_b", sess.id)
    rt.state.pending_user_messages.clear()
    assert bridge.take_note(sess.id) is False
    # TTL: a stale note is dropped from the pending list and not appended
    await bridge._barge_in(peer, "rtc_b", sess.id)
    stamp, lst = bridge.notes[sess.id]
    bridge.notes[sess.id] = (stamp - rtc_bridge.NOTE_TTL_S - 1, lst)
    assert bridge.take_note(sess.id) is False and rt.state.pending_user_messages == []
    # the timer path
    monkeypatch.setattr(rtc_bridge, "NOTE_TTL_S", 0.05)
    await bridge._barge_in(peer, "rtc_b", sess.id)
    assert rt.state.pending_user_messages == [SPEECH_INTERRUPTED_NOTE]
    await asyncio.sleep(0.15)
    assert rt.state.pending_user_messages == [] and sess.id not in bridge.notes
    assert coord.cancelled == []


async def test_turn_is_interrupted_only_when_configured(rig: dict[str, Any]) -> None:
    svc, sess, coord, peer, bridge = rig["svc"], rig["sess"], rig["coord"], rig["peer"], rig["bridge"]
    svc.settings.voice_barge_in_interrupts_turn = True
    peer.say(tone_pcm(5.0, amp=0.3))
    peer.speech.started_at = time.monotonic() - 1.0
    feed(bridge, user_voice(0.3), sess.id, peer)
    await until(lambda: bool(coord.cancelled))
    assert coord.cancelled == [sess.id] and not peer.speaking
    feed(bridge, user_voice(0.3) + bytes(16_000 * 2), sess.id)
    await until(lambda: bool(coord.submitted))
    assert coord.submitted[0][2] == "followup"  # the turn is gone: a new followup
    barge = [
        e for e in await svc.events.replay(0, frozenset({f"session:{sess.id}"})) if e.type == "voice.barge_in"
    ]
    assert barge[0].data["interrupted_turn"] is True
