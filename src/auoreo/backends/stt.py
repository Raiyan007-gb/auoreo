"""Speech-to-text backend kind (`backends/stt/<name>/`, M4c).

Input is always a normalized WAV (16 kHz, mono, int16; see `auoreo.voice.audio.normalize`).
Cloud backends enforce `MAX_UPLOAD_BYTES` before sending anything.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.tools.context import ToolError

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
"""OpenAI/Groq upload cap; enforced before any cloud request (spec §25.1)."""


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TranscriptDelta:
    """One streaming update (G6): `text` is the new piece; `final` marks a finished segment.
    The last item of a stream has `done=True` and carries the whole transcript in `text`."""

    text: str
    final: bool = False
    done: bool = False
    language: str | None = None


class SttBackend(ABC):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus:
        """Library/model/key present? Never raises."""

    @abstractmethod
    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        """Transcribe a 16 kHz mono int16 WAV. Raises `ToolError` with a remedy on failure."""

    streaming: bool = False
    """True when `stream` yields partial deltas from the provider (not just one final item)."""

    async def stream(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> AsyncIterator[TranscriptDelta]:
        """Streaming deltas; the default runs `transcribe` and yields one `done` item."""
        t = await self.transcribe(wav, language=language, keyterms=keyterms)
        yield TranscriptDelta(t.text, final=True, done=True, language=t.language)


def check_upload(wav: bytes, limit: int = MAX_UPLOAD_BYTES) -> None:
    """The 25 MB pre-check every cloud STT backend runs before opening a connection."""
    if len(wav) > limit:
        raise ToolError(f"audio is {len(wav)} bytes; the cloud STT limit is {limit} bytes (25 MB)")
