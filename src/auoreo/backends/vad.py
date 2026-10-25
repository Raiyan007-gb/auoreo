"""Voice-activity detection backend kind (`backends/vad/<name>/`, M4c).

Used as the silence gate before STT (`speech_segments` on a whole WAV) and for live
endpointing / barge-in over WebRTC audio (`is_speech` on ~20–30 ms PCM frames).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from auoreo.backends import BackendStatus


class VadBackend(ABC):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def speech_segments(self, wav: bytes) -> list[tuple[float, float]]:
        """Speech regions `(start_s, end_s)` of a 16 kHz mono int16 WAV; `[]` = silence."""

    @abstractmethod
    def is_speech(self, frame_pcm16: bytes, sample_rate: int) -> bool:
        """Whether one short little-endian int16 mono frame contains speech (fast, sync)."""
