"""Wire models for web-UI helpers (G5b): RTC client capabilities and the context gauge."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RtcCapabilities(BaseModel):
    """What a browser call client may ask for in `POST /api/rtc/offer`."""

    available: bool = Field(description="aiortc is installed (else offers answer 501)")
    voice_enabled: bool = Field(description="`settings.voice_enabled`: live transcription runs")
    speak: bool = Field(
        description="An outbound speech track is attached unless the offer says `speak: false`"
    )
    teleop_input_enabled: bool = Field(
        description="`settings.teleop_input_enabled`: offers may set `input: true` (`auoreo-input` channel)"
    )
    livekit: bool = Field(
        default=False,
        description="I8: the LiveKit transport is selected and `i8.livekit.web_toggle` is on "
        "(call panel toggle; tokens from `POST /api/livekit/token`)",
    )
    livekit_agent: str | None = Field(
        default=None,
        description="I8: agent name to dispatch (`room_config.agents`) when the LiveKit worker runs",
    )


class SessionContext(BaseModel):
    """Context occupancy of a session's next request (the `ctx_viz` gauge).

    `pressure` is the token count of the assembled request (system prompt + history) as the
    compaction meter counts it; `tools_tokens` is the tool-schema overhead sent alongside."""

    session_id: str
    model: str
    context_window: int
    system_tokens: int
    tools_tokens: int
    messages_tokens: int
    pressure: int
    auto_compact_at: int = Field(description="Pressure at which automatic compaction runs")
    ratio: float = Field(description="`(pressure + tools_tokens) / context_window`, 0..1+")
