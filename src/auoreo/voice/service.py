"""The voice control plane (M4c): push-to-talk transcription, speech synthesis, readiness.

`transcribe`: normalize (ffmpeg) → VAD gate (`NoSpeech` on silence) → STT backend.
`speak`: `prepare_spoken_text` → sentence chunks under the TTS backend's `chunk_limit` →
synthesize each → concatenate (WAV chunks are joined sample-exactly; other containers are
byte-concatenated and keep the first chunk's media type).

Backends are the selected `stt`/`tts`/`vad`/`wake` instances from `Services.backend`, so
audio libraries load only when a real backend is used. Live WebRTC audio goes through
`auoreo.voice.rtc_bridge` (attached to `services.rtc` by `attach`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.backends.stt import SttBackend, Transcript
from auoreo.backends.tts import Audio, TtsBackend, clamp_speed
from auoreo.backends.vad import VadBackend
from auoreo.tools.context import ToolError
from auoreo.voice.audio import NoSpeech, VoiceUnavailable, concat_wavs, duration_s, normalize
from auoreo.voice.text import chunk_text, prepare_spoken_text

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.voice.rtc_bridge import RtcVoiceBridge

log = logging.getLogger(__name__)

KINDS = ("stt", "tts", "vad", "wake", "desktop")
MAX_SPEAK_CHARS = 5000


@dataclass
class TranscribeResult:
    text: str
    segments: list[dict[str, Any]]
    language: str | None
    duration_s: float
    backend: str
    speech: list[tuple[float, float]] = field(default_factory=list)


class VoiceService:
    def __init__(self, services: Services) -> None:
        self.svc = services
        self.bridge: RtcVoiceBridge | None = None

    # ------------------------------------------------------------ backends
    def backend_name(self, kind: str) -> str:
        bdef = self.svc.harness.select_backend(kind, self.svc.settings.backends.get(kind))
        return bdef.manifest.name if bdef is not None else ""

    def _backend(self, kind: str, cls: type[Any]) -> Any:
        try:
            inst = self.svc.backend(kind)
        except (KeyError, LookupError) as exc:
            raise VoiceUnavailable(str(exc)) from exc
        if not isinstance(inst, cls):
            raise VoiceUnavailable(f"{kind} backend {type(inst).__name__} does not implement {cls.__name__}")
        return inst

    @property
    def stt(self) -> SttBackend:
        stt: SttBackend = self._backend("stt", SttBackend)
        return stt

    @property
    def tts(self) -> TtsBackend:
        tts: TtsBackend = self._backend("tts", TtsBackend)
        return tts

    @property
    def vad(self) -> VadBackend:
        vad: VadBackend = self._backend("vad", VadBackend)
        return vad

    # ------------------------------------------------------------ transcribe
    async def transcribe_wav(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        """STT on an already-normalized WAV (no VAD gate, no event)."""
        return await self.stt.transcribe(
            wav, language=language or self.svc.settings.voice_language, keyterms=keyterms
        )

    async def transcribe(
        self,
        data: bytes,
        mime: str | None = None,
        *,
        language: str | None = None,
        keyterms: Sequence[str] = (),
        session_id: str | None = None,
    ) -> TranscribeResult:
        wav = await normalize(data, mime)
        dur = duration_s(wav)
        speech = await self.vad.speech_segments(wav)
        if not speech:
            raise NoSpeech("no speech detected")
        t = await self.transcribe_wav(wav, language=language, keyterms=keyterms)
        backend = self.backend_name("stt")
        await self.svc.emit(
            session_id or "server",
            "voice.transcribed",
            {"session_id": session_id, "text": t.text, "duration_s": round(dur, 3), "backend": backend},
        )
        segs = [{"start": s.start, "end": s.end, "text": s.text} for s in t.segments]
        return TranscribeResult(t.text, segs, t.language, round(dur, 3), backend, speech)

    # ----------------------------------------------------------------- speak
    async def speak(
        self, text: str, *, voice: str | None = None, speed: float = 1.0, session_id: str | None = None
    ) -> Audio:
        if len(text) > MAX_SPEAK_CHARS:
            raise ToolError(f"text is {len(text)} chars; the limit is {MAX_SPEAK_CHARS}")
        spoken = prepare_spoken_text(text)
        if not spoken:
            raise ToolError("nothing to speak after removing markdown, code and emoji")
        tts = self.tts
        voice = voice or self.svc.settings.voice_tts_voice
        speed = clamp_speed(speed)
        parts = [
            await tts.synthesize(c, voice=voice, speed=speed) for c in chunk_text(spoken, tts.chunk_limit)
        ]
        if len(parts) == 1:
            audio = parts[0]
        elif all(p.media_type == "audio/wav" for p in parts):
            audio = Audio(concat_wavs([p.data for p in parts]), "audio/wav", parts[0].sample_rate)
        else:
            audio = Audio(b"".join(p.data for p in parts), parts[0].media_type, parts[0].sample_rate)
        await self.svc.emit(
            session_id or "server", "voice.spoke", {"chars": len(spoken), "backend": self.backend_name("tts")}
        )
        return audio

    # ---------------------------------------------------------------- status
    async def status(self) -> list[dict[str, Any]]:
        """Selected backend + `check()` per voice/desktop kind (never raises)."""
        rows = await asyncio.gather(*(self._status(kind) for kind in KINDS))
        return list(rows)

    async def _status(self, kind: str) -> dict[str, Any]:
        row: dict[str, Any] = {"kind": kind, "name": None, "ok": False, "detail": "", "remedy": ""}
        try:
            bdef = self.svc.harness.select_backend(kind, self.svc.settings.backends.get(kind))
        except KeyError as exc:
            return {**row, "detail": str(exc), "remedy": f"fix settings.backends.{kind}"}
        if bdef is None:
            return {**row, "detail": "no backend selected", "remedy": f"set settings.backends.{kind}"}
        m = bdef.manifest
        row.update(name=m.name, license=m.license, posture=m.posture)
        try:
            st = await asyncio.wait_for(self.svc.backend(kind).check(), 10)
        except Exception as exc:  # a broken backend must not break status
            return {**row, "detail": f"{type(exc).__name__}: {exc}"[:300]}
        return {**row, "ok": bool(st.ok), "detail": st.detail, "remedy": st.remedy}

    # ------------------------------------------------------------------- rtc
    def attach(self, rtc_manager: Any) -> None:
        """Install the live-audio hook on an `RtcManager` (idempotent)."""
        from auoreo.voice.rtc_bridge import RtcVoiceBridge

        if self.bridge is None:
            self.bridge = RtcVoiceBridge(self)
        self.bridge.attach(rtc_manager)

    async def close(self) -> None:
        if self.bridge is not None:
            await self.bridge.close()
