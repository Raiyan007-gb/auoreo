"""Wake-word backend kind (`backends/wake/<name>/`, M4c). Push-to-talk is the default;
continuous listening only runs behind a wake hit (spec §13.6)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class WakeHit:
    phrase: str
    start: float = 0.0
    score: float = 1.0


class WakeBackend(ABC):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @property
    def phrase(self) -> str:
        return str(getattr(self.settings, "voice_wake_phrase", "hey oreo"))

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def detect(self, wav: bytes) -> WakeHit | None:
        """Look for `phrase` in a 16 kHz mono int16 WAV."""
