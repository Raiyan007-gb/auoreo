"""Audio fixtures generated at test time (stdlib only; no binary files in the repo)."""

from __future__ import annotations

import io
import math
import wave
from array import array

from auoreo.voice.audio import wav_from_pcm


def tone_pcm(
    seconds: float, rate: int = 16_000, channels: int = 1, freq: float = 440.0, amp: float = 0.5
) -> bytes:
    n = int(seconds * rate)
    step = 2 * math.pi * freq / rate
    samples = array("h")
    for i in range(n):
        v = int(amp * 32767 * math.sin(i * step))
        samples.extend([v] * channels)
    return samples.tobytes()


def tone_wav(
    seconds: float = 1.0, rate: int = 16_000, channels: int = 1, comment: str = "", amp: float = 0.5
) -> bytes:
    pcm = tone_pcm(seconds, rate, channels, amp=amp)
    if comment:
        return wav_from_pcm(pcm, rate, channels, comment)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def silence_wav(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    return wav_from_pcm(b"\0\0" * int(seconds * rate), rate)
