"""ElevenLabs text-to-speech (`POST /v1/text-to-speech/{voice_id}?output_format=opus_48000_64`).

Model `eleven_flash_v2_5` (40k chars per request, spec §25.3). `voice` is an ElevenLabs
voice id; the default is the documented example voice. `speed` maps to
`voice_settings.speed`, which ElevenLabs accepts in 0.7–1.2.
"""

from __future__ import annotations

from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend
from auoreo.voice.cloud import key_status, post_json_audio, require_key

URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
KEY = "ELEVENLABS_API_KEY"
MODEL = "eleven_flash_v2_5"
OUTPUT_FORMAT = "opus_48000_64"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"


class Backend(TtsBackend):
    chunk_limit = 40_000

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"ElevenLabs {MODEL}")

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        key = require_key(self.settings, KEY)
        body: dict[str, Any] = {"text": text, "model_id": MODEL}
        if speed != 1.0:
            body["voice_settings"] = {"speed": min(1.2, max(0.7, float(speed)))}
        data, _ = await post_json_audio(
            "elevenlabs",
            URL.format(voice_id=voice or DEFAULT_VOICE),
            {"xi-api-key": key, "Accept": "audio/ogg"},
            body,
            params={"output_format": OUTPUT_FORMAT},
        )
        return Audio(data, "audio/ogg", 48_000)
