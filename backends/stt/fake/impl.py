"""Test STT backend: returns the WAV's `ICMT` comment (LIST/INFO chunk) as the transcript, or
"fake transcript (N.N s)" when there is none. No model, no network."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript
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
