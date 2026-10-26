"""Mistral Voxtral text-to-speech over httpx (spec §25.3).

`POST https://api.mistral.ai/v1/audio/speech` with `{model, input, voice_id, response_format}`
answers JSON `{"audio_data": "<base64>"}`. Cap 4000 chars per request (the voice service chunks
below `chunk_limit`). A 403 is Mistral's moderation refusal: it is reported as
`moderation_403` so the caller can fall back (never retried here). The key travels only as a
header and never reaches errors, logs or events. 30 s timeout, no retries (spec §24.5).
"""

from __future__ import annotations

import base64
import binascii

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend
from auoreo.core.settings_i8 import i8_of
from auoreo.voice.audio import AudioError, VoiceUnavailable
from auoreo.voice.cloud import TIMEOUT_S, _error, key_status, require_key

KEY = "MISTRAL_API_KEY"
FORMATS = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}


class Backend(TtsBackend):
    chunk_limit = 4000
    response_format = "mp3"

    def _voice(self, voice: str | None) -> str | None:
        configured = i8_of(self.settings).tts.mistral_voice
        return voice or configured or getattr(self.settings, "voice_tts_voice", None)

    async def check(self) -> BackendStatus:
        status = key_status(self.settings, KEY, f"Mistral {i8_of(self.settings).tts.mistral_model}")
        if status.ok and not self._voice(None):
            return BackendStatus(False, "no Mistral voice_id configured", remedy="set i8.tts.mistral_voice")
        return status

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        key = require_key(self.settings, KEY)
        cfg = i8_of(self.settings).tts
        voice_id = self._voice(voice)
        if not voice_id:
            raise VoiceUnavailable("tts/mistral needs a voice_id; set i8.tts.mistral_voice")
        body = {
            "model": cfg.mistral_model,
            "input": text[: self.chunk_limit],
            "voice_id": voice_id,
            "response_format": self.response_format,
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.post(cfg.mistral_url, headers={"Authorization": f"Bearer {key}"}, json=body)
        except httpx.HTTPError as exc:
            raise VoiceUnavailable(f"mistral request failed: {type(exc).__name__}") from None
        if r.status_code == 403:
            raise VoiceUnavailable("mistral TTS refused the text (moderation_403)")
        if r.status_code >= 400:
            raise _error("mistral", r)
        try:
            data = base64.b64decode(str(r.json()["audio_data"]), validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise AudioError("mistral TTS returned no audio_data") from None
        if not data:
            raise AudioError("mistral TTS returned empty audio_data")
        return Audio(data, FORMATS[self.response_format], None)
