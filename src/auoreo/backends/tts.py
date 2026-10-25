"""Text-to-speech backend kind (`backends/tts/<name>/`, M4c).

`chunk_limit` is the provider's per-request character cap (spec §25.3: kitten 2k, piper 5k,
openai 4096, elevenlabs flash 40k); the voice service splits text at sentence boundaries
below it and concatenates the results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from auoreo.backends import BackendStatus

SPEED_MIN, SPEED_MAX = 0.25, 4.0


@dataclass(frozen=True)
class Audio:
    data: bytes
    media_type: str
    """`audio/wav`, `audio/mpeg` or `audio/ogg` (Opus)."""
    sample_rate: int | None = None


class TtsBackend(ABC):
    chunk_limit: int = 2000

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        """Synthesize one chunk (≤ `chunk_limit` chars). Raises `ToolError` on failure."""


def clamp_speed(speed: float) -> float:
    return min(SPEED_MAX, max(SPEED_MIN, float(speed)))
