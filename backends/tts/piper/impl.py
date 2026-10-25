"""Piper stub (GPL-3.0 → subprocess-only posture). Reports honestly; never imports piper."""

from __future__ import annotations

import shutil

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend
from auoreo.voice.audio import VoiceUnavailable

NOT_IMPLEMENTED = "the piper subprocess adapter is not implemented yet; use tts kittentts or fake"


class Backend(TtsBackend):
    chunk_limit = 5000

    async def check(self) -> BackendStatus:
        if shutil.which("piper") is None:
            return BackendStatus(
                False, "piper is not installed (GPL-3.0, subprocess-only)", remedy=NOT_IMPLEMENTED
            )
        return BackendStatus(False, "piper binary found but not wired", remedy=NOT_IMPLEMENTED)

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        raise VoiceUnavailable(f"tts/piper: {NOT_IMPLEMENTED}")
