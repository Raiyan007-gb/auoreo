"""ElevenLabs Scribe speech-to-text over httpx multipart (`xi-api-key` header)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.cloud import key_status, require_key
from auoreo.voice.cloud_stt import post_form, voice_g6, words_to_segments

URL = "https://api.elevenlabs.io/v1/speech-to-text"
KEY = "ELEVENLABS_API_KEY"
MAX_KEYTERMS = 100


class Backend(SttBackend):
    @property
    def model(self) -> str:
        return voice_g6(self.settings).elevenlabs_stt_model

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"ElevenLabs {self.model}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        fields: list[tuple[str, object | None]] = [
            ("model_id", self.model),
            ("language_code", language),
            ("timestamps_granularity", "word"),
            ("tag_audio_events", "false"),
        ]
        fields += [("keyterms", t) for t in [k for k in keyterms if k.strip()][:MAX_KEYTERMS]]
        body = await post_form("elevenlabs", URL, {"xi-api-key": key}, wav, fields, self.settings)
        text = str(body.get("text", "")).strip()
        segs = [Segment(**s) for s in words_to_segments(body.get("words") or [], text)]
        conf = body.get("language_probability")
        return Transcript(text, segs, body.get("language_code") or language, float(conf) if conf else None)
