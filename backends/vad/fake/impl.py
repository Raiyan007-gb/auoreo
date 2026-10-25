"""Energy VAD: RMS over 30 ms frames against `settings.voice_speaking_rms`. Good enough to gate
silence and to endpoint tones in tests; it cannot tell speech from noise (use `vad/silero`)."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.vad import VadBackend
from auoreo.voice.audio import read_wav, rms

FRAME_MS = 30
MIN_SPEECH_S = 0.1
MERGE_GAP_S = 0.3


class Backend(VadBackend):
    is_test_backend = True

    @property
    def threshold(self) -> float:
        return float(getattr(self.settings, "voice_speaking_rms", 0.02))

    async def check(self) -> BackendStatus:
        return BackendStatus(True, f"energy VAD (RMS > {self.threshold}; no model)")

    def is_speech(self, frame_pcm16: bytes, sample_rate: int) -> bool:
        return rms(frame_pcm16) > self.threshold

    async def speech_segments(self, wav: bytes) -> list[tuple[float, float]]:
        info = read_wav(wav)
        rate = info.sample_rate
        step = int(rate * FRAME_MS / 1000) * 2 * info.channels
        segs: list[list[float]] = []
        for i in range(0, len(info.pcm), step):
            if not self.is_speech(info.pcm[i : i + step], rate):
                continue
            start = i / (2 * info.channels) / rate
            end = min(len(info.pcm), i + step) / (2 * info.channels) / rate
            if segs and start - segs[-1][1] <= MERGE_GAP_S:
                segs[-1][1] = end
            else:
                segs.append([start, end])
        return [(round(a, 3), round(b, 3)) for a, b in segs if b - a >= MIN_SPEECH_S]
