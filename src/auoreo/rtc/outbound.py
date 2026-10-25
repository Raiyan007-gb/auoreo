"""Outbound (TTS) audio for a WebRTC peer (F1, spec §13 "the agent speaks back").

`SpeechQueue` is pure Python: `say(pcm, rate)` resamples int16 mono PCM to 48 kHz (what the
track sends) and to 16 kHz (the echo-guard reference), splits both into 20 ms frames and
queues them; `clear()` drops everything at once (barge-in) and bumps `generation`, so a
speaker still synthesizing later sentences knows to stop.

`outbound_track(queue)` builds an aiortc `AudioStreamTrack` subclass that pulls one frame per
20 ms: exact pacing against a wall-clock origin, `pts` = samples sent at 48 kHz,
`time_base = 1/48000`, silence when the queue is empty. Every played frame (silence too) is
fed to the peer's `EchoGuard`.
"""

from __future__ import annotations

import asyncio
import fractions
import time
from array import array
from collections import deque
from typing import Any

from auoreo.rtc.echo import REF_RATE, EchoGuard

OUT_RATE = 48_000
FRAME_MS = 20
OUT_SAMPLES = OUT_RATE * FRAME_MS // 1000  # 960
REF_SAMPLES = REF_RATE * FRAME_MS // 1000  # 320
SILENCE = bytes(OUT_SAMPLES * 2)
REF_SILENCE = bytes(REF_SAMPLES * 2)


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of little-endian int16 mono PCM."""
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    if src_rate == dst_rate or not pcm:
        return pcm
    n_in = len(pcm) // 2
    n_out = max(1, round(n_in * dst_rate / src_rate))
    try:
        import numpy as np

        x = np.frombuffer(pcm, dtype="<i2").astype("float64")
        pos = np.arange(n_out) * (src_rate / dst_rate)
        y = np.interp(pos, np.arange(n_in), x)
        return bytes(np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes())
    except ImportError:  # pragma: no cover - numpy ships with the rtc extra
        src = array("h")
        src.frombytes(pcm)
        out = array("h", bytes(n_out * 2))
        step = src_rate / dst_rate
        for i in range(n_out):
            p = i * step
            j = min(int(p), n_in - 1)
            k = min(j + 1, n_in - 1)
            out[i] = int(round(src[j] + (src[k] - src[j]) * (p - j)))
        return out.tobytes()


def _frames(pcm: bytes, size: int) -> list[bytes]:
    nbytes = size * 2
    out = [pcm[i : i + nbytes] for i in range(0, len(pcm), nbytes)]
    if out and len(out[-1]) < nbytes:
        out[-1] = out[-1] + bytes(nbytes - len(out[-1]))
    return out


class SpeechQueue:
    def __init__(self, echo: EchoGuard | None = None) -> None:
        self.frames: deque[tuple[bytes, bytes]] = deque()
        self.echo = echo
        self.generation = 0
        self.started_at: float | None = None
        """Monotonic time the current run of speech was queued (barge-in grace origin)."""
        self.frames_played = 0

    @property
    def speaking(self) -> bool:
        return bool(self.frames)

    def say(self, pcm: bytes, sample_rate: int = REF_RATE) -> float:
        """Queue int16 mono PCM; returns the queued duration in seconds."""
        out = _frames(resample(pcm, sample_rate, OUT_RATE), OUT_SAMPLES)
        ref = _frames(resample(pcm, sample_rate, REF_RATE), REF_SAMPLES)
        ref += [REF_SILENCE] * (len(out) - len(ref))
        if not out:
            return 0.0
        if not self.frames:
            self.started_at = time.monotonic()
        self.frames.extend(zip(out, ref, strict=False))
        return len(out) * FRAME_MS / 1000

    def clear(self) -> int:
        """Drop queued audio immediately; returns the number of 20 ms frames dropped."""
        n = len(self.frames)
        self.frames.clear()
        self.generation += 1
        self.started_at = None
        return n

    def next_frame(self, now: float | None = None) -> bytes:
        """The next 48 kHz 20 ms frame (silence when idle); feeds the echo reference."""
        now = time.monotonic() if now is None else now
        if self.frames:
            out, ref = self.frames.popleft()
            self.frames_played += 1
            audible = True
        else:
            out, ref, audible = SILENCE, REF_SILENCE, False
            self.started_at = None
        if self.echo is not None:
            self.echo.feed(ref, audible, now)
        return out


_TRACK_CLASS: type[Any] | None = None


def _track_class() -> type[Any]:
    global _TRACK_CLASS
    if _TRACK_CLASS is not None:
        return _TRACK_CLASS
    from aiortc import AudioStreamTrack
    from aiortc.mediastreams import MediaStreamError
    from av import AudioFrame

    class OutboundAudioTrack(AudioStreamTrack):
        """48 kHz mono s16, 20 ms frames from a `SpeechQueue`; silence when it is empty."""

        def __init__(self, queue: SpeechQueue) -> None:
            super().__init__()
            self.queue = queue
            self._origin: float | None = None
            self._sent = 0

        async def recv(self) -> Any:
            if self.readyState != "live":
                raise MediaStreamError
            if self._origin is None:
                self._origin = time.time()
            else:
                wait = self._origin + self._sent / OUT_RATE - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
            frame = AudioFrame(format="s16", layout="mono", samples=OUT_SAMPLES)
            frame.planes[0].update(self.queue.next_frame())
            frame.pts = self._sent
            frame.sample_rate = OUT_RATE
            frame.time_base = fractions.Fraction(1, OUT_RATE)
            self._sent += OUT_SAMPLES
            return frame

    _TRACK_CLASS = OutboundAudioTrack
    return OutboundAudioTrack


def outbound_track(queue: SpeechQueue) -> Any:
    """An aiortc `MediaStreamTrack` (kind `audio`) reading from `queue`."""
    return _track_class()(queue)
