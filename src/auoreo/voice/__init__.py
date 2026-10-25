"""Voice (M4c): audio helpers, the voice service, and the WebRTC transcription bridge.

`start(services)` is the extension starter (`services.ext["voice"]`); it imports no audio
library — backends load lazily on first use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.voice.service import VoiceService


async def start(services: Services) -> VoiceService | None:
    if not services.settings.voice_enabled:
        return None
    from auoreo.voice.service import VoiceService

    voice = VoiceService(services)
    if services.rtc is not None:
        voice.attach(services.rtc)
    return voice


def voice_for(services: Services) -> VoiceService:
    """The started voice service, or a fresh one (e.g. a CLI run without extensions)."""
    from auoreo.voice.audio import VoiceUnavailable
    from auoreo.voice.service import VoiceService

    if not services.settings.voice_enabled:
        raise VoiceUnavailable("voice is disabled (settings.voice_enabled = false)")
    existing = services.ext.get("voice")
    return existing if isinstance(existing, VoiceService) else VoiceService(services)
