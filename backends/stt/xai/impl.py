"""xAI speech-to-text — experimental lane, gated by config + key (spec §25: never treat the
path as normative; it lives in `settings.g6.voice.xai_stt_url`)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, check_upload
from auoreo.voice.audio import VoiceUnavailable
from auoreo.voice.cloud import key_status, require_key, segments_of
from auoreo.voice.cloud_stt import post_form, voice_g6

KEY = "XAI_API_KEY"
GATE = 'set "g6": {"voice": {"xai_experimental": true}} in settings.json'


class Backend(SttBackend):
    async def check(self) -> BackendStatus:
        cfg = voice_g6(self.settings)
        if not cfg.xai_experimental:
            return BackendStatus(False, "xAI STT is experimental and disabled", remedy=GATE)
        return key_status(self.settings, KEY, f"xAI STT (experimental) {cfg.xai_stt_url}")

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        cfg = voice_g6(self.settings)
        if not cfg.xai_experimental:
            raise VoiceUnavailable(f"stt/xai is experimental and disabled; {GATE}")
        check_upload(wav)
        key = require_key(self.settings, KEY)
        headers = {"Authorization": f"Bearer {key}"}
        body = await post_form("xai", cfg.xai_stt_url, headers, wav, [("language", language)])
        segs = [Segment(**s) for s in segments_of(body)]
        return Transcript(str(body.get("text", "")).strip(), segs, body.get("language") or language, None)
