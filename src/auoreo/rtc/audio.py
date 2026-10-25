"""Inbound audio plumbing: decode → 16 kHz mono s16 → RMS level per window (no STT yet).

`consume_audio` drains a remote audio track; every resampled chunk goes to the optional
frames hook (the future STT entry point) and into a `LevelMeter`. `av`/`numpy` arrive with
the `rtc` extra and are imported lazily.
"""

from __future__ import annotations

import inspect
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

LevelCallback = Callable[[float], Awaitable[None]]
FramesHook = Callable[[Any], Any]
"""Called with each 16 kHz mono int16 numpy chunk; may be sync or async."""


class LevelMeter:
    """Accumulates int16 PCM; `ready()` once a full window is in and the spacing allows."""

    def __init__(self, interval_s: float = 1.0, rate: int = SAMPLE_RATE) -> None:
        self.interval_s = max(0.05, interval_s)
        self.window = int(rate * self.interval_s)
        self.sumsq = 0.0
        self.count = 0
        self.last_emit = float("-inf")

    def add(self, samples: Any) -> None:
        x = samples.astype("float64").reshape(-1) / 32768.0
        self.sumsq += float((x * x).sum())
        self.count += int(x.size)

    def ready(self, now: float) -> bool:
        return self.count >= self.window and now - self.last_emit >= self.interval_s

    def flush(self, now: float) -> float:
        rms = math.sqrt(self.sumsq / self.count) if self.count else 0.0
        self.sumsq, self.count, self.last_emit = 0.0, 0, now
        return rms


async def consume_audio(
    track: Any,
    meter: LevelMeter,
    on_level: LevelCallback,
    frames_hook: Callable[[], FramesHook | None] = lambda: None,
) -> None:
    """`track.recv()` until the track ends; emits at most one level per meter interval."""
    import av
    from aiortc.mediastreams import MediaStreamError

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    while True:
        try:
            frame = await track.recv()
        except MediaStreamError:
            return
        for out in resampler.resample(frame):
            pcm = out.to_ndarray().reshape(-1)
            await _call_hook(frames_hook(), pcm)
            meter.add(pcm)
        now = time.monotonic()
        if meter.ready(now):
            await on_level(meter.flush(now))


async def drain_track(track: Any) -> None:
    """Consume a track we do not use (e.g. video) so its receive queue cannot grow."""
    from aiortc.mediastreams import MediaStreamError

    while True:
        try:
            await track.recv()
        except MediaStreamError:
            return


async def _call_hook(hook: FramesHook | None, pcm: Any) -> None:
    if hook is None:
        return
    try:
        res = hook(pcm)
        if inspect.isawaitable(res):
            await res
    except Exception:  # an STT backend bug must not stop level metering
        log.exception("rtc on_audio_frames hook failed")
