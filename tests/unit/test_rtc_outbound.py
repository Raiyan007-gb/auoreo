"""Outbound speech: resampling, the speech queue, and the paced aiortc track (F1)."""

from __future__ import annotations

import time
from array import array

import pytest

from auoreo.rtc.echo import EchoGuard
from auoreo.rtc.outbound import (
    OUT_RATE,
    OUT_SAMPLES,
    REF_SAMPLES,
    SILENCE,
    SpeechQueue,
    outbound_track,
    resample,
)
from auoreo.voice.audio import rms
from tests.audio import tone_pcm


def test_resample_lengths_and_energy() -> None:
    pcm = tone_pcm(0.5, rate=16_000, amp=0.5)
    up = resample(pcm, 16_000, 48_000)
    assert len(up) // 2 == 24_000
    assert abs(rms(up) - rms(pcm)) < 0.01
    down = resample(up, 48_000, 16_000)
    assert len(down) == len(pcm)
    assert resample(pcm, 16_000, 16_000) == pcm
    assert resample(b"", 16_000, 48_000) == b""
    a = array("h")
    a.frombytes(resample(array("h", [0, 1000]).tobytes(), 1, 2))
    assert list(a)[:3] == [0, 500, 1000]  # linear interpolation


def test_queue_frames_silence_fill_and_clear() -> None:
    echo = EchoGuard()
    q = SpeechQueue(echo)
    assert not q.speaking and q.next_frame() == SILENCE
    secs = q.say(tone_pcm(0.11), 16_000)  # 5.5 frames → 6, last one padded
    assert secs == pytest.approx(0.12) and len(q.frames) == 6 and q.speaking
    assert q.started_at is not None
    f = q.next_frame()
    assert len(f) == OUT_SAMPLES * 2 and rms(f) > 0.2
    assert len(echo.ref[-1]) == REF_SAMPLES * 2 and echo.active(time.monotonic())
    gen = q.generation
    assert q.clear() == 5
    assert not q.speaking and q.generation == gen + 1 and q.started_at is None
    assert q.next_frame() == SILENCE


async def test_track_pacing_pts_and_stop_speaking() -> None:
    pytest.importorskip("aiortc")
    q = SpeechQueue()
    track = outbound_track(q)
    assert track.kind == "audio"
    q.say(tone_pcm(0.1), 16_000)  # 5 frames
    t0 = time.monotonic()
    frames = [await track.recv() for _ in range(11)]
    elapsed = time.monotonic() - t0
    assert 0.17 <= elapsed <= 0.4, elapsed  # 10 waits of 20 ms
    assert [f.pts for f in frames] == [i * OUT_SAMPLES for i in range(11)]
    assert all(f.sample_rate == OUT_RATE and f.samples == OUT_SAMPLES for f in frames)
    assert str(frames[0].time_base) == f"1/{OUT_RATE}"
    loud = [rms(bytes(f.planes[0])[: OUT_SAMPLES * 2]) > 0.1 for f in frames]
    assert loud == [True] * 5 + [False] * 6  # then silence fill
    q.say(tone_pcm(1.0), 16_000)
    await track.recv()
    assert q.clear() == 49 and not q.speaking
    nxt = await track.recv()
    assert rms(bytes(nxt.planes[0])[: OUT_SAMPLES * 2]) == 0.0  # cut immediately
    track.stop()


def test_peer_say_and_stop_speaking_emits() -> None:
    from auoreo.rtc.peers import Peer

    spawned: list[object] = []

    class _Svc:
        def emit(self, agg: str, kind: str, data: dict[str, object]) -> tuple[str, str, dict[str, object]]:
            return (agg, kind, data)

    class _Mgr:
        svc = _Svc()

        def _spawn(self, coro: object) -> None:
            spawned.append(coro)

    peer = Peer(rtc_id="rtc_a", session_id="s1", pc=None, manager=_Mgr())
    assert peer.say(tone_pcm(0.1)) == 0.0 and peer.stop_speaking() == 0  # not a speaking peer
    peer.speech = SpeechQueue()
    assert peer.say(tone_pcm(0.1)) == pytest.approx(0.1) and peer.speaking
    assert peer.stop_speaking("barge_in") == 5 and not peer.speaking
    assert spawned == [
        (
            "s1",
            "voice.speech_cancelled",
            {"rtc_id": "rtc_a", "session_id": "s1", "reason": "barge_in", "dropped_ms": 100},
        )
    ]
    assert peer.stop_speaking() == 0 and len(spawned) == 1  # nothing queued: no event
