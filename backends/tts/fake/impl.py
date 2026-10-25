"""Test TTS backend: a 440 Hz sine WAV (16 kHz mono int16) of `len(text) / 15` seconds whose
ICMT comment carries the text, so `stt/fake` transcribes it back."""

from __future__ import annotations

import math
from array import array

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend, clamp_speed
from auoreo.voice.audio import SAMPLE_RATE, wav_from_pcm

CHARS_PER_SECOND = 15
MAX_SECONDS = 600


def tone(seconds: float, freq: float = 440.0, rate: int = SAMPLE_RATE, amp: float = 0.3) -> bytes:
    n = max(1, int(min(seconds, MAX_SECONDS) * rate))
    step = 2 * math.pi * freq / rate
    return array("h", (int(amp * 32767 * math.sin(i * step)) for i in range(n))).tobytes()


class Backend(TtsBackend):
    is_test_backend = True
    chunk_limit = 2000

    def __init__(self, settings: object) -> None:
        super().__init__(settings)
        self.calls: list[dict[str, object]] = []

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake tts (test backend; 440 Hz tone)")

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        seconds = len(text) / CHARS_PER_SECOND / clamp_speed(speed)
        return Audio(wav_from_pcm(tone(seconds), SAMPLE_RATE, 1, comment=text), "audio/wav", SAMPLE_RATE)
