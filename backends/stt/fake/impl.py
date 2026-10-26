"""Test STT backend: returns the WAV's `ICMT` comment (LIST/INFO chunk) as the transcript, or
"fake transcript (N.N s)" when there is none. No model, no network.

With `streaming` set, `stream()` yields the transcript word by word (one partial delta per word, then `done`);
tests flip `backend.streaming = True` to exercise the streaming paths."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, TranscriptDelta
from auoreo.voice.audio import duration_s, read_wav


def fake_text(wav: bytes) -> str:
    comment = read_wav(wav).comment.strip()
    return comment or f"fake transcript ({duration_s(wav):.1f} s)"


class Backend(SttBackend):
    is_test_backend = True

    def __init__(self, settings: object) -> None:
        super().__init__(settings)
        self.calls: list[dict[str, object]] = []

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake stt (test backend; WAV comment or duration)")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        self.calls.append({"bytes": len(wav), "language": language, "keyterms": list(keyterms)})
        text = fake_text(wav)
        return Transcript(text, [Segment(0.0, round(duration_s(wav), 3), text)], language or "en", 1.0)

    async def stream(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> AsyncIterator[TranscriptDelta]:
        t = await self.transcribe(wav, language=language, keyterms=keyterms)
        words = t.text.split() if self.streaming else []
        for i, w in enumerate(words):
            yield TranscriptDelta(w if i == 0 else f" {w}", language=t.language)
        yield TranscriptDelta(t.text, final=True, done=True, language=t.language)
