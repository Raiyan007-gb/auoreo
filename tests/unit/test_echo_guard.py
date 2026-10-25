"""Echo guard: correlation against the outbound reference, tail window, threshold margin (F1)."""

from __future__ import annotations

import pytest

from auoreo.rtc.echo import EchoGuard
from auoreo.rtc.outbound import SpeechQueue
from tests.audio import tone_pcm

FRAME = 640  # 20 ms at 16 kHz, bytes


def _noise(seconds: float, seed: int = 1) -> bytes:
    import numpy as np

    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * 16_000)) * 6000).astype("<i2").tobytes()


def _feed(guard: EchoGuard, pcm: bytes, now: float) -> None:
    for i in range(0, len(pcm), FRAME):
        guard.feed(pcm[i : i + FRAME].ljust(FRAME, b"\0"), True, now)


def test_delayed_copy_is_echo_other_audio_is_not() -> None:
    pytest.importorskip("numpy")
    g = EchoGuard()
    ref = _noise(1.0)
    _feed(g, ref, now=10.0)
    delayed = ref[16_000 - 2 * 3_517 : 16_000 - 2 * 3_517 + FRAME]  # an odd lag, mid-buffer
    assert g.correlation(delayed) > 0.99
    import numpy as np

    attenuated = (np.frombuffer(delayed, dtype="<i2") // 4).astype("<i2").tobytes()
    assert g.correlation(attenuated) > 0.99  # level-independent
    assert g.is_echo(delayed, 0.8, now=10.2) and g.dropped == 1
    assert g.correlation(_noise(0.02, seed=7)) < 0.5  # the user's voice
    assert g.correlation(tone_pcm(0.02, freq=1000.0)) < 0.5
    assert g.correlation(bytes(FRAME)) == 0.0  # silence never "correlates"


def test_windows_tail_and_recency() -> None:
    g = EchoGuard(window_s=1.0, tail_s=0.3)
    assert not g.active(0.0) and not g.recent(0.0) and g.correlation(tone_pcm(0.02)) == 0.0
    g.feed(tone_pcm(0.02), True, 5.0)
    g.feed(bytes(FRAME), False, 5.02)  # silence fill does not extend the window
    assert g.active(5.29) and not g.active(5.31)
    assert g.recent(5.9) and not g.recent(6.1)
    assert not g.is_echo(tone_pcm(0.02), 0.8, now=6.5)  # stale reference: never echo
    assert len(EchoGuard(window_s=1.0).ref) == 0 and EchoGuard(window_s=1.0).ref.maxlen == 50


def test_queue_feeds_reference_as_played() -> None:
    pytest.importorskip("numpy")
    g = EchoGuard()
    q = SpeechQueue(g)
    out = tone_pcm(0.2, freq=440.0, amp=0.3)
    q.say(out, 16_000)
    for _ in range(10):
        q.next_frame(now=1.0)
    assert len(g.ref) == 10 and g.active(1.1)
    assert g.correlation(out[3200:3840]) > 0.95
