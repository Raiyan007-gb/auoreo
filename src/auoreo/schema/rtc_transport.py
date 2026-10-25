"""Wire models for `POST /api/rtc/join` (transport-agnostic, W2-D): the selected
`rtc_transport` backend answers with an SDP (aiortc, P2P) or a room URL + token (LiveKit)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RtcJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="Session the media belongs to")
    identity: str = Field(
        default="user",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.@:-]+$",
        description="Participant identity (LiveKit token subject)",
    )
    sdp: str | None = Field(default=None, description="SDP offer; required by the aiortc transport")


class RtcJoinInfo(BaseModel):
    transport: str = Field(description="Backend name, e.g. `aiortc` or `livekit`")
    join_id: str = Field(description="Pass to `DELETE /api/rtc/join/{join_id}` to leave")
    session_id: str | None = None
    sdp: str | None = Field(default=None, description="SDP answer (P2P transports)")
    url: str | None = Field(default=None, description="SFU URL to connect to (LiveKit)")
    token: str | None = Field(default=None, description="Room-scoped access token (LiveKit)")
    room: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RtcLeft(BaseModel):
    transport: str
    join_id: str
