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
import re
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
MAX_TRANSLATE_CHARS = 20_000
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
TRANSLATE_PROMPT = (
    "You are a translation engine. Translate the user's text into the language with BCP-47 tag "
    "{target}{source}. Preserve meaning, tone, names, numbers, code and formatting. Answer with "
    "the translation only: no quotes, notes or explanations."
)


async def translate_text(services: Services, text: str, target: str, source: str | None = None) -> str:
    """One aux call (`purpose=translate`, temperature 0, the `small` group when configured)."""
    from auoreo.gateway.aux import resolve_aux_model
    from auoreo.gateway.types import LLMRequest, TextDelta

    for tag in (target, source):
        if tag is not None and not _BCP47.match(tag):
            raise ToolError(f"'{tag}' is not a BCP-47 language tag (e.g. de, pt-BR, zh-Hans)")
    if not text.strip():
        raise ToolError("nothing to translate")
    if len(text) > MAX_TRANSLATE_CHARS:
        raise ToolError(f"text is {len(text)} chars; the limit is {MAX_TRANSLATE_CHARS}")
    st = services.settings
    model = resolve_aux_model(st, "translate", gateway=services.gateway)
    system = TRANSLATE_PROMPT.format(target=target, source=f" from {source}" if source else "")
    request = LLMRequest(
        model=model,
        messages=({"role": "system", "content": system}, {"role": "user", "content": text}),
        tool_choice="none",
        temperature=0.0,
        max_tokens=min(8192, len(text) // 2 + 256),
        metadata={"purpose": "translate", "target": target},
    )
    parts = [item.text async for item in services.gateway.stream(request) if isinstance(item, TextDelta)]
    return "".join(parts).strip()


@dataclass
class TranscribeResult:
    text: str
    segments: list[dict[str, Any]]
    language: str | None
    duration_s: float
    backend: str
    speech: list[tuple[float, float]] = field(default_factory=list)
    translation: str | None = None


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
        translate_to: str | None = None,
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
        res = TranscribeResult(t.text, segs, t.language, round(dur, 3), backend, speech)
        if translate_to and t.text.strip():
            src = t.language if t.language and _BCP47.match(t.language) else None
            res.translation = await translate_text(self.svc, t.text, translate_to, src)
        return res

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
