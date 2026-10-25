"""Groq speech-to-text (OpenAI-compatible multipart, `whisper-large-v3-turbo`)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.cloud import key_status, post_multipart, require_key, segments_of

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
KEY = "GROQ_API_KEY"
DEFAULT_MODEL = "whisper-large-v3-turbo"
MODELS = frozenset({"whisper-large-v3-turbo", "whisper-large-v3"})


class Backend(SttBackend):
    @property
    def model(self) -> str:
        chosen = getattr(self.settings, "voice_stt_model", None)
        return chosen if chosen in MODELS else DEFAULT_MODEL

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"Groq {self.model}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        body = await post_multipart(
            "groq",
            URL,
            key,
            wav,
            {
                "model": self.model,
                "response_format": "verbose_json",
                "temperature": "0",
                "language": language,
                "prompt": " ".join(keyterms)[:800] or None,
            },
        )
        segs = [Segment(**s) for s in segments_of(body)]
        return Transcript(str(body.get("text", "")).strip(), segs, body.get("language") or language, None)
