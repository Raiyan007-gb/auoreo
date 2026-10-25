"""`/api/voice` — push-to-talk transcription, speech synthesis and readiness (M4c).

Media for live conversations travels over WebRTC (`/api/rtc`); these endpoints are the
batch/push-to-talk path: upload a recording, get text (and optionally a followup run).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import VoiceBackendStatus, VoiceSpeakRequest, VoiceStatus
from auoreo.schema.voice_meeting import VoiceTranscription
from auoreo.tools.context import ToolError
from auoreo.voice.audio import MAX_INPUT_BYTES, AudioTooLarge, NoSpeech, VoiceUnavailable, transcode
from auoreo.voice.service import VoiceService

router = APIRouter(prefix="/api/voice", tags=["voice"], dependencies=AUTH)

# First match wins: specific ToolError subclasses before the base class.
_ERRORS: dict[type[Exception], int] = {
    AudioTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    NoSpeech: status.HTTP_422_UNPROCESSABLE_CONTENT,
    VoiceUnavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    ToolError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}
_FORMAT_OF = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "opus"}


def _voice(svc: Services) -> VoiceService:
    voice = svc.ext.get("voice")
    if not isinstance(voice, VoiceService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "voice is disabled (settings.voice_enabled)")
    return voice


def _http(exc: Exception) -> HTTPException:
    code = next(c for t, c in _ERRORS.items() if isinstance(exc, t))
    return HTTPException(code, str(exc))


@router.post(
    "/transcribe",
    response_model=VoiceTranscription,
    responses={
        413: {"description": "Upload over 25 MB"},
        422: {"description": "No speech detected, or undecodable audio"},
        503: {"description": "Voice disabled or the STT backend is not ready"},
    },
)
async def transcribe(
    svc: ServicesDep,
    file: Annotated[UploadFile, File(description="Audio (wav, webm, ogg, mp3, m4a, …)")],
    language: Annotated[str | None, Form()] = None,
    session_id: Annotated[str | None, Form()] = None,
    language_q: Annotated[str | None, Query(alias="language")] = None,
    session_id_q: Annotated[str | None, Query(alias="session_id")] = None,
    translate_to: Annotated[str | None, Form(description="BCP-47 tag: also return `translation`")] = None,
    translate_to_q: Annotated[str | None, Query(alias="translate_to")] = None,
) -> VoiceTranscription:
    """Normalize → VAD gate → STT. With `session_id` the text is also submitted as a followup;
    with `translate_to` the transcript is also translated (`translation`)."""
    voice = _voice(svc)
    sid = session_id or session_id_q
    if sid is not None and await svc.get_session(sid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    data = await file.read(MAX_INPUT_BYTES + 1)
    try:
        res = await voice.transcribe(
            data,
            file.content_type,
            language=language or language_q,
            session_id=sid,
            translate_to=translate_to or translate_to_q,
        )
    except ToolError as exc:
        raise _http(exc) from None
    run_id = None
    if sid is not None and res.text.strip():
        run_id = await svc.coordinator.submit(sid, res.text, "followup")
    return VoiceTranscription(
        text=res.text,
        segments=res.segments,
        language=res.language,
        duration_s=res.duration_s,
        backend=res.backend,
        run_id=run_id,
        translation=res.translation,
    )


@router.post(
    "/speak",
    response_class=Response,
    responses={
        200: {"content": {"audio/wav": {}, "audio/mpeg": {}, "audio/ogg": {}}, "description": "Audio bytes"},
        422: {"description": "Nothing to speak or synthesis failed"},
        503: {"description": "Voice disabled or the TTS backend is not ready"},
    },
)
async def speak(body: VoiceSpeakRequest, svc: ServicesDep) -> Response:
    """Text (≤ 5000 chars) → audio in the backend's format, or `format` via ffmpeg."""
    voice = _voice(svc)
    try:
        audio = await voice.speak(body.text, voice=body.voice, speed=body.speed, session_id=body.session_id)
        data, media_type = audio.data, audio.media_type
        if body.format is not None and _FORMAT_OF.get(media_type) != body.format:
            data, media_type = await transcode(data, body.format)
    except ToolError as exc:
        raise _http(exc) from None
    headers = {"X-Voice-Backend": voice.backend_name("tts")}
    return Response(content=data, media_type=media_type, headers=headers)


@router.get("/status", response_model=VoiceStatus)
async def voice_status(svc: ServicesDep) -> VoiceStatus:
    """Selected stt/tts/vad/wake/desktop backends and their readiness (`check()`)."""
    voice = svc.ext.get("voice")
    if not isinstance(voice, VoiceService):
        return VoiceStatus(enabled=False, backends=[])
    rows = await voice.status()
    return VoiceStatus(enabled=True, backends=[VoiceBackendStatus(**r) for r in rows])
