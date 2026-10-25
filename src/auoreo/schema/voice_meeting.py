"""Wire models for meeting mode (`/api/voice/meetings`, F4) and `transcribe` + translation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auoreo.schema.chat import VoiceTranscription as _VoiceTranscription

MeetingStatus = Literal["recording", "finalising", "finalised", "failed"]


class VoiceTranscription(_VoiceTranscription):
    """`POST /api/voice/transcribe` response. Same OpenAPI name as the M4c model it extends
    (only this one is referenced), so existing clients keep their `VoiceTranscription` type."""

    translation: str | None = Field(default=None, description="Present when `translate_to` was given")


class MeetingStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Meeting", min_length=1, max_length=200)
    session_id: str | None = Field(default=None, description="Session for the meeting events")
    language: str | None = Field(default=None, max_length=35, description="STT language hint (ISO-639-1)")
    speak: bool = Field(default=False, description="P2: the agent may speak (only with an attached RTC peer)")
    rtc_id: str | None = Field(default=None, description="Live mode: append this RTC peer's utterances")
    num_speakers: int | None = Field(default=None, ge=1, le=32, description="Known speaker count")


class MeetingStarted(BaseModel):
    meeting_id: str


class MeetingAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rtc_id: str = Field(min_length=1, max_length=200)


class MeetingSegment(BaseModel):
    start: float
    end: float
    speaker: str = Field(description="Diarization label, e.g. SPEAKER_00")
    text: str
    confidence: float | None = None
    tone: str | None = Field(default=None, description="Tone classifier label for the utterance")


class MeetingChunkInfo(BaseModel):
    meeting_id: str
    index: int
    duration_s: float
    total_s: float


class MeetingInfo(BaseModel):
    id: str
    title: str
    status: MeetingStatus
    session_id: str | None = None
    language: str | None = None
    speak: bool = False
    live: bool = Field(default=False, description="An RTC peer is attached (live utterances)")
    created_at: datetime
    finalised_at: datetime | None = None
    chunks: int = 0
    duration_s: float = 0.0
    speakers: list[str] = Field(default_factory=list)
    error: str | None = None
    summary: str | None = None
    summary_session_id: str | None = None
    ingested: bool = False


class MeetingDetail(MeetingInfo):
    transcript: list[MeetingSegment] = Field(default_factory=list)
    markdown: str = ""


class MeetingSummarizeStarted(BaseModel):
    run_id: str
    session_id: str
