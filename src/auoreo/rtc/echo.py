"""Echo guard for full-duplex voice (spec §25.5): the agent's own voice coming back through
the user's microphone must not be transcribed or mistaken for a barge-in.

`EchoGuard` keeps the last `window_s` (1 s) of what the outbound track actually played, as
16 kHz mono int16 frames (silence included, so the timeline stays contiguous). Two checks:

- `active(now)`: outbound audio is playing or stopped less than `tail_s` (300 ms) ago; the
  bridge then multiplies the VAD/RMS speech threshold by `Settings.voice_echo_margin`.
- `correlation(frame)`: the maximum normalised cross-correlation of an inbound frame against
  every lag of the reference (FFT, numpy). A frame above `Settings.voice_echo_corr` is a
  delayed copy of our own output and is dropped. Without numpy the correlation is 0.0 (the
  threshold margin still applies).
"""

from __future__ import annotations

import math
from collections import deque

REF_RATE = 16_000
FRAME_MS = 20
_EPS = 1e-9


class EchoGuard:
    def __init__(self, window_s: float = 1.0, tail_s: float = 0.3) -> None:
        self.tail_s = tail_s
        self.window_s = window_s
        self.ref: deque[bytes] = deque(maxlen=max(1, int(window_s * 1000 / FRAME_MS)))
        self.last_audio_at = -math.inf
        self.dropped = 0

    def feed(self, ref_frame: bytes, audible: bool, now: float) -> None:
        """One played 20 ms frame (16 kHz int16); `audible` = it carried speech, not fill."""
        self.ref.append(ref_frame)
        if audible:
            self.last_audio_at = now

    def active(self, now: float) -> bool:
        return now - self.last_audio_at <= self.tail_s

    def recent(self, now: float) -> bool:
        """The reference still holds audible output (within `window_s`)."""
        return now - self.last_audio_at <= self.window_s

    def correlation(self, frame: bytes) -> float:
        if not self.ref or len(frame) < 4:
            return 0.0
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy ships with the rtc extra
            return 0.0
        x = np.frombuffer(frame[: len(frame) - len(frame) % 2], dtype="<i2").astype("float64")
        r = np.frombuffer(b"".join(self.ref), dtype="<i2").astype("float64")
        m, n = x.size, r.size
        ex = float((x * x).sum())
        if n < m or ex < _EPS * m * 32768.0:
            return 0.0
        size = 1 << (n + m - 1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(r, size) * np.conj(np.fft.rfft(x, size)), size)[: n - m + 1]
        cs = np.concatenate(([0.0], np.cumsum(r * r)))
        er = cs[m:] - cs[: n - m + 1]
        floor = max(er.max(), _EPS) * 1e-3  # ignore near-silent reference windows
        ncc = np.where(er > floor, np.abs(corr) / np.sqrt(er * ex + _EPS), 0.0)
        return float(min(1.0, ncc.max()))

    def is_echo(self, frame: bytes, threshold: float, now: float) -> bool:
        if not self.recent(now) or self.correlation(frame) < threshold:
            return False
        self.dropped += 1
        return True
