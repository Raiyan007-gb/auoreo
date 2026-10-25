"""`/api/voice/meetings` — meeting mode (F4): record → finalise → speaker-labelled transcript → summary.

Audio arrives as multipart uploads (chunks are appended; one full recording works too) or
live from a WebRTC peer attached with `rtc_id`. See `auoreo.voice.meeting`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.voice_meeting import (
    MeetingAttachRequest,
    MeetingChunkInfo,
    MeetingDetail,
    MeetingInfo,
    MeetingStarted,
    MeetingStartRequest,
    MeetingSummarizeStarted,
)
from auoreo.tools.context import ToolError
from auoreo.voice.audio import MAX_INPUT_BYTES, AudioTooLarge, NoSpeech, VoiceUnavailable
from auoreo.voice.meeting import MeetingNotFound, MeetingService, MeetingStateError, meetings_for

router = APIRouter(prefix="/api/voice/meetings", tags=["voice"], dependencies=AUTH)

# First match wins: specific ToolError subclasses before the base class.
_ERRORS: dict[type[Exception], int] = {
    MeetingNotFound: status.HTTP_404_NOT_FOUND,
    MeetingStateError: status.HTTP_409_CONFLICT,
    AudioTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    NoSpeech: status.HTTP_422_UNPROCESSABLE_CONTENT,
    VoiceUnavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    ToolError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}
_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown meeting"},
    409: {"description": "The meeting is not in a state that allows this"},
    503: {"description": "Voice disabled or a backend is not ready"},
}


def _meetings(svc: Services) -> MeetingService:
    if not svc.settings.voice_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "voice is disabled (settings.voice_enabled)")
    return meetings_for(svc)


def _http(exc: Exception) -> HTTPException:
    code = next(c for t, c in _ERRORS.items() if isinstance(exc, t))
    detail = "meeting not found" if isinstance(exc, MeetingNotFound) else str(exc)
    return HTTPException(code, detail)


@router.get("", response_model=list[MeetingInfo])
async def list_meetings(svc: ServicesDep) -> list[MeetingInfo]:
    """All meetings, newest first (transcripts omitted; `GET /{id}` has them)."""
    return [MeetingInfo(**m) for m in _meetings(svc).list_meetings()]


@router.post("", response_model=MeetingStarted, status_code=201, responses=_RESPONSES)
async def start_meeting(body: MeetingStartRequest, svc: ServicesDep) -> MeetingStarted:
    """Start a listen-only meeting (`speak: true` lets the agent answer over an attached RTC peer)."""
    meetings = _meetings(svc)
    if body.session_id is not None and await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    meta = await meetings.start(
        body.title,
        session_id=body.session_id,
        language=body.language,
        speak=body.speak,
        rtc_id=body.rtc_id,
        num_speakers=body.num_speakers,
    )
    return MeetingStarted(meeting_id=meta["id"])


@router.get("/{meeting_id}", response_model=MeetingDetail, responses=_RESPONSES)
async def get_meeting(meeting_id: str, svc: ServicesDep) -> MeetingDetail:
    try:
        return MeetingDetail(**_meetings(svc).detail(meeting_id))
    except MeetingNotFound as exc:
        raise _http(exc) from None


@router.post(
    "/{meeting_id}/audio",
    response_model=MeetingChunkInfo,
    responses={**_RESPONSES, 413: {"description": "Chunk over 25 MB"}, 422: {"description": "Undecodable"}},
)
async def upload_audio(
    meeting_id: str,
    svc: ServicesDep,
    file: Annotated[UploadFile, File(description="Audio chunk or a full recording (wav, webm, ogg, mp3, …)")],
) -> MeetingChunkInfo:
    """Append audio to a recording meeting (normalized to 16 kHz mono WAV)."""
    meetings = _meetings(svc)
    data = await file.read(MAX_INPUT_BYTES + 1)
    try:
        return MeetingChunkInfo(**await meetings.add_audio(meeting_id, data, file.content_type))
    except (MeetingNotFound, ToolError) as exc:
        raise _http(exc) from None


@router.post("/{meeting_id}/attach", response_model=MeetingInfo, responses=_RESPONSES)
async def attach_peer(meeting_id: str, body: MeetingAttachRequest, svc: ServicesDep) -> MeetingInfo:
    """Live mode: utterances from this RTC peer are appended instead of submitted as turns."""
    try:
        return MeetingInfo(**_meetings(svc).attach(meeting_id, body.rtc_id))
    except (MeetingNotFound, ToolError) as exc:
        raise _http(exc) from None


@router.post("/{meeting_id}/stop", response_model=MeetingDetail, responses=_RESPONSES)
async def stop_meeting(meeting_id: str, svc: ServicesDep) -> MeetingDetail:
    """Finalise: VAD → diarize → STT per speaker turn → redacted, speaker-labelled transcript."""
    try:
        return MeetingDetail(**await _meetings(svc).stop(meeting_id))
    except (MeetingNotFound, ToolError) as exc:
        raise _http(exc) from None


@router.post("/{meeting_id}/summarize", response_model=MeetingSummarizeStarted, responses=_RESPONSES)
async def summarize_meeting(meeting_id: str, svc: ServicesDep) -> MeetingSummarizeStarted:
    """Run the `meeting-summary` workflow in a new session (watch `run_id` on `/api/events`)."""
    try:
        run_id, session_id = await _meetings(svc).summarize(meeting_id)
    except (MeetingNotFound, ToolError) as exc:
        raise _http(exc) from None
    return MeetingSummarizeStarted(run_id=run_id, session_id=session_id)
