"""Voice wire models (`/api/voice/*`, spec §13), split out of `schema/chat.py`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceSegment(BaseModel):
    start: float
    end: float
    text: str


class VoiceTranscription(BaseModel):
    text: str
    segments: list[VoiceSegment]
    language: str | None = None
    duration_s: float = Field(description="Duration of the normalized audio")
    backend: str = Field(description="Selected stt backend")
    run_id: str | None = Field(default=None, description="Followup run when `session_id` was given")


class VoiceSpeakRequest(_In):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = Field(default=None, max_length=200)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    format: Literal["wav", "mp3", "opus"] | None = Field(
        default=None, description="Transcode the backend output (ffmpeg); default = backend native"
    )
    session_id: str | None = None


class VoiceBackendStatus(BaseModel):
    kind: str
    name: str | None = None
    ok: bool
    detail: str = ""
    remedy: str = ""
    license: str = ""
    posture: str = "ok"


class VoiceStatus(BaseModel):
    enabled: bool
    backends: list[VoiceBackendStatus]
