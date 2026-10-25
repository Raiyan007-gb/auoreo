"""Heuristic diarizer: no model, deterministic.

1. Energy gate: 30 ms frames whose RMS exceeds `voice_speaking_rms` are voiced; voiced runs
   separated by less than `GAP_S` of silence form one segment (shorter than 0.1 s: dropped).
2. Each segment gets a profile `(log10 RMS, 10 × zero-crossing rate)`. A segment joins the
   nearest existing speaker when the distance is under `JOIN_DIST`, else it opens a new one
   (capped at `num_speakers` or `MAX_SPEAKERS`; past the cap the nearest wins). Adjacent
   segments of the same speaker merge into one turn.

ZCR is a crude spectral-brightness cue, not pitch tracking; loudness separates near and far
talkers. Two tones of different frequency or level come out as two speakers.
"""

from __future__ import annotations

import math
from array import array

from auoreo.backends import BackendStatus
from auoreo.backends.diarize import DiarizeBackend, Turn, relabel
from auoreo.voice.audio import read_wav, rms

FRAME_MS = 30
GAP_S = 0.3
MIN_SEG_S = 0.1
JOIN_DIST = 0.25
MAX_SPEAKERS = 4


def zcr(pcm: bytes) -> float:
    try:
        import numpy as np

        x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2")
        return float(np.count_nonzero((x[1:] < 0) != (x[:-1] < 0)) / (len(x) - 1)) if len(x) > 1 else 0.0
    except ImportError:
        pass
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if len(a) < 2:
        return 0.0
    crossings = sum(1 for i in range(1, len(a)) if (a[i - 1] < 0) != (a[i] < 0))
    return crossings / (len(a) - 1)


def segments(pcm: bytes, rate: int, threshold: float) -> list[tuple[float, float]]:
    step = int(rate * FRAME_MS / 1000) * 2
    segs: list[list[float]] = []
    for i in range(0, len(pcm), step):
        if rms(pcm[i : i + step]) <= threshold:
            continue
        start, end = i / 2 / rate, min(len(pcm), i + step) / 2 / rate
        if segs and start - segs[-1][1] < GAP_S:
            segs[-1][1] = end
        else:
            segs.append([start, end])
    return [(a, b) for a, b in segs if b - a >= MIN_SEG_S]


def profile(pcm: bytes) -> tuple[float, float]:
    return math.log10(max(rms(pcm), 1e-6)), zcr(pcm) * 10.0


class Backend(DiarizeBackend):
    is_test_backend = True

    @property
    def threshold(self) -> float:
        return float(getattr(self.settings, "voice_speaking_rms", 0.02))

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "heuristic diarizer (energy gaps + loudness/ZCR profiles; no model)")

    async def diarize(self, wav16k_mono: bytes, *, num_speakers: int | None = None) -> list[Turn]:
        info = read_wav(wav16k_mono)
        rate = info.sample_rate
        cap = max(1, num_speakers or MAX_SPEAKERS)
        centroids: list[tuple[float, float]] = []
        raw: list[tuple[float, float, int]] = []
        for start, end in segments(info.pcm, rate, self.threshold):
            p = profile(info.pcm[int(start * rate) * 2 : int(end * rate) * 2])
            dists = [math.dist(p, c) for c in centroids]
            best = min(range(len(dists)), key=dists.__getitem__) if dists else -1
            if best < 0 or (dists[best] > JOIN_DIST and len(centroids) < cap):
                centroids.append(p)
                best = len(centroids) - 1
            if raw and raw[-1][2] == best:
                raw[-1] = (raw[-1][0], end, best)
            else:
                raw.append((start, end, best))
        return relabel(list(raw))
