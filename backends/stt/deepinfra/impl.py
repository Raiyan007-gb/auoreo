"""DeepInfra speech-to-text through its OpenAI-compatible lane (`<base>/audio/transcriptions`)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.cloud import key_status, require_key, segments_of
from auoreo.voice.cloud_stt import post_form, voice_g6

KEY = "DEEPINFRA_API_KEY"


class Backend(SttBackend):
    @property
    def model(self) -> str:
        return voice_g6(self.settings).deepinfra_model

    @property
    def url(self) -> str:
        return voice_g6(self.settings).deepinfra_base_url.rstrip("/") + "/audio/transcriptions"

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"DeepInfra {self.model}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        fields: list[tuple[str, object | None]] = [
            ("model", self.model),
            ("response_format", "verbose_json"),
            ("language", language),
            ("prompt", " ".join(keyterms)[:800] or None),
        ]
        body = await post_form(
            "deepinfra", self.url, {"Authorization": f"Bearer {key}"}, wav, fields, self.settings
        )
        segs = [Segment(**s) for s in segments_of(body)]
        return Transcript(str(body.get("text", "")).strip(), segs, body.get("language") or language, None)
