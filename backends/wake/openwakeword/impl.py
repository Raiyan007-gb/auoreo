"""openWakeWord stub (non-commercial weights → `nc-opt-in`). Reports honestly; no adapter."""

from __future__ import annotations

import importlib.util

from auoreo.backends import BackendStatus
from auoreo.backends.wake import WakeBackend, WakeHit
from auoreo.voice.audio import VoiceUnavailable

NOT_IMPLEMENTED = "the openwakeword adapter is not implemented; use wake sherpa_onnx (Apache-2.0)"


class Backend(WakeBackend):
    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("openwakeword") is None:
            return BackendStatus(
                False, "openwakeword is not installed (NC weights, opt-in)", remedy=NOT_IMPLEMENTED
            )
        return BackendStatus(False, "openwakeword installed but not wired", remedy=NOT_IMPLEMENTED)

    async def detect(self, wav: bytes) -> WakeHit | None:
        raise VoiceUnavailable(f"wake/openwakeword: {NOT_IMPLEMENTED}")
