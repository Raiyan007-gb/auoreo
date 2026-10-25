"""OpenAI text-to-speech (`POST /v1/audio/speech`, `gpt-4o-mini-tts`).

`input` ≤ 4096 chars per request (the voice service chunks below it); `response_format`
is `mp3` (class attribute; `opus` gives Ogg/Opus). `POST /api/voice/speak` with `format`
transcodes the result through ffmpeg when another container is wanted.
"""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend, clamp_speed
from auoreo.voice.cloud import key_status, post_json_audio, require_key

URL = "https://api.openai.com/v1/audio/speech"
KEY = "OPENAI_API_KEY"
MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "marin"
FORMATS = {"mp3": "audio/mpeg", "opus": "audio/ogg"}


class Backend(TtsBackend):
    chunk_limit = 4096
    response_format = "mp3"

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"OpenAI {MODEL}")

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        key = require_key(self.settings, KEY)
        body = {
            "model": MODEL,
            "input": text[: self.chunk_limit],
            "voice": voice or DEFAULT_VOICE,
            "response_format": self.response_format,
            "speed": clamp_speed(speed),
        }
        data, _ = await post_json_audio("openai", URL, {"Authorization": f"Bearer {key}"}, body)
        return Audio(data, FORMATS[self.response_format], None)
