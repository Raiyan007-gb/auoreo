"""OpenAI speech-to-text over httpx multipart (`POST /v1/audio/transcriptions`).

`gpt-4o-transcribe` / `gpt-4o-mini-transcribe` only answer `json` (text, no segments);
`whisper-1` gets `verbose_json` + `timestamp_granularities[]=segment`. Keyterms go in
`prompt`. A model id from another lane falls back to the lane default (spec §25.1).
"""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.cloud import key_status, post_multipart, require_key, segments_of

URL = "https://api.openai.com/v1/audio/transcriptions"
KEY = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-4o-transcribe"
MODELS = frozenset({"whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe", "gpt-transcribe"})
VERBOSE = frozenset({"whisper-1"})


class Backend(SttBackend):
    @property
    def model(self) -> str:
        chosen = getattr(self.settings, "voice_stt_model", None)
        return chosen if chosen in MODELS else DEFAULT_MODEL

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"OpenAI {self.model}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        verbose = self.model in VERBOSE
        fields: dict[str, object] = {
            "model": self.model,
            "response_format": "verbose_json" if verbose else "json",
            "language": language,
            "prompt": " ".join(keyterms)[:800] or None,
        }
        if verbose:
            fields["timestamp_granularities"] = ["segment"]
        body = await post_multipart("openai", URL, key, wav, fields)
        segs = [Segment(**s) for s in segments_of(body)]
        return Transcript(str(body.get("text", "")).strip(), segs, body.get("language") or language, None)
