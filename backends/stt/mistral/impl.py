"""Mistral speech-to-text (Voxtral) over httpx multipart.

`timestamp_granularities` and `language` are mutually exclusive in the API; AUOREO asks for
segment timestamps and lets Voxtral detect the language (spec §25 deltas). Keyterms become
`context_bias` fields, one per term, at most 100.
"""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.cloud import key_status, require_key, segments_of
from auoreo.voice.cloud_stt import post_form, voice_g6

URL = "https://api.mistral.ai/v1/audio/transcriptions"
KEY = "MISTRAL_API_KEY"
MAX_BIAS = 100


class Backend(SttBackend):
    @property
    def model(self) -> str:
        return voice_g6(self.settings).mistral_model

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"Mistral {self.model}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        fields: list[tuple[str, object | None]] = [
            ("model", self.model),
            ("timestamp_granularities", "segment"),
        ]
        terms = [t.strip().replace(" ", "_") for t in keyterms if t.strip()][:MAX_BIAS]
        fields += [("context_bias", t) for t in terms]
        body = await post_form("mistral", URL, {"x-api-key": key}, wav, fields)
        segs = [Segment(**s) for s in segments_of(body)]
        return Transcript(str(body.get("text", "")).strip(), segs, body.get("language") or language, None)
