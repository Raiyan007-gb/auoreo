"""Test wake word: phrase substring match on the fake transcript (the WAV's ICMT comment)."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.wake import WakeBackend, WakeHit
from auoreo.voice.audio import read_wav


class Backend(WakeBackend):
    is_test_backend = True

    async def check(self) -> BackendStatus:
        return BackendStatus(True, f"fake wake word '{self.phrase}' (transcript substring)")

    async def detect(self, wav: bytes) -> WakeHit | None:
        text = " ".join(read_wav(wav).comment.lower().split())
        phrase = " ".join(self.phrase.lower().split())
        if phrase and phrase in text:
            return WakeHit(self.phrase, 0.0, 1.0)
        return None
