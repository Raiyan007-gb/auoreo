"""TTS through the voice media sidecar (`auoreo.voice.sidecar`)."""

from __future__ import annotations

import base64

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend
from auoreo.voice.sidecar import get_host


class Backend(TtsBackend):
    chunk_limit = 2000  # conservative: the smallest local engine cap (kitten 2k)

    async def check(self) -> BackendStatus:
        return await get_host(self.settings).check()

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        r = await get_host(self.settings).call("synthesize", {"text": text, "voice": voice, "speed": speed})
        return Audio(base64.b64decode(r["audio_b64"]), str(r["media_type"]), r.get("sample_rate"))

    async def close(self) -> None:
        await get_host(self.settings).close()
