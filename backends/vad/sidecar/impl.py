"""VAD through the voice media sidecar (`auoreo.voice.sidecar`).

`speech_segments` (the silence gate before STT) runs in the sidecar. `is_speech` is called
synchronously per 20-30 ms RTC frame, where a process round trip is not an option, so it stays
local: the energy test against `settings.voice_speaking_rms` (same as `vad/fake`)."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.vad import VadBackend
from auoreo.voice.audio import rms
from auoreo.voice.sidecar import encode, get_host


class Backend(VadBackend):
    async def check(self) -> BackendStatus:
        return await get_host(self.settings).check()

    async def speech_segments(self, wav: bytes) -> list[tuple[float, float]]:
        r = await get_host(self.settings).call("vad", {"wav_b64": encode(wav)})
        return [(float(a), float(b)) for a, b in r.get("segments") or []]

    def is_speech(self, frame_pcm16: bytes, sample_rate: int) -> bool:
        return rms(frame_pcm16) > float(getattr(self.settings, "voice_speaking_rms", 0.02))

    async def close(self) -> None:
        await get_host(self.settings).close()
