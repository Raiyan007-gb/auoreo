"""Microphone (audio input) backend kind (`backends/audio_input/<name>/`, I8).

Used by the wake daemon (`auoreo.voice.wake`): `frames()` yields little-endian int16 mono PCM
chunks at `SAMPLE_RATE` (16 kHz) for as long as the stream is open. Real devices
(`sounddevice`) are imported lazily inside `frames()`; `check()` never opens the device for
long (spec §25.5: the doctor mic probe opens/closes with a timeout, never at import).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from auoreo.backends import BackendStatus

SAMPLE_RATE = 16_000
FRAME_MS = 30


class AudioInputBackend(ABC):
    sample_rate: int = SAMPLE_RATE

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    def frames(self, frame_ms: int = FRAME_MS) -> AsyncIterator[bytes]:
        """Yield int16 mono PCM chunks of ~`frame_ms` until `close()`."""

    async def close(self) -> None:
        return None
