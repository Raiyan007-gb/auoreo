"""Speaker-diarization backend kind (`backends/diarize/<name>/`, F4).

Input is a normalized WAV (16 kHz, mono, int16; `auoreo.voice.audio.normalize`). Output is
the list of speaker turns, sorted by start time, with stable labels (`SPEAKER_00`, …) in
order of first appearance. Meeting mode (`auoreo.voice.meeting`) runs STT on each turn.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DiarizeBackend(ABC):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus:
        """Library/model/token present? Never raises."""

    @abstractmethod
    async def diarize(self, wav16k_mono: bytes, *, num_speakers: int | None = None) -> list[Turn]:
        """Speaker turns of a 16 kHz mono int16 WAV; `[]` = no speech."""


def relabel(raw: list[tuple[float, float, Any]]) -> list[Turn]:
    """Sort by start and map backend labels to `SPEAKER_00…` in order of first appearance."""
    names: dict[Any, str] = {}
    out: list[Turn] = []
    for start, end, label in sorted(raw, key=lambda t: (t[0], t[1])):
        name = names.setdefault(label, f"SPEAKER_{len(names):02d}")
        out.append(Turn(round(float(start), 3), round(float(end), 3), name))
    return out
