"""Wire models for `POST /api/rtc/join` (transport-agnostic, W2-D): the selected
`rtc_transport` backend answers with an SDP (aiortc, P2P) or a room URL + token (LiveKit)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auoreo.schema.chat import RtcOffer as _ChatRtcOffer


class RtcJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="Session the media belongs to")
    identity: str = Field(
        default="user",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.@:-]+$",
        deprecated=True,
        description="Ignored: the server mints an opaque uuid4 participant identity (spec §26.10)",
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


class RtcOffer(_ChatRtcOffer):
    """Client SDP offer. The client must create the `auoreo-events` data channel first.

    F1: offer an audio m-line (`sendrecv`, or `recvonly` to only listen) to hear the agent;
    `input: true` additionally accepts an `auoreo-input` data channel (teleop)."""

    speak: bool | None = Field(
        default=None,
        description="`false` opts this peer out of spoken replies (default: `settings.voice_speak_over_rtc`)",
    )
    input: bool = Field(
        default=False,
        description="Accept the `auoreo-input` teleop channel; 403 unless `settings.teleop_input_enabled`",
    )


# --- F5: ICE servers for browser peers (static STUN/TURN + ephemeral TURN REST credentials)
class RtcIceServer(BaseModel):
    """One `RTCIceServer` entry, ready for `new RTCPeerConnection({iceServers})`."""

    urls: list[str]
    username: str | None = Field(default=None, description="TURN REST username `<expiry>:<user>`")
    credential: str | None = Field(
        default=None, description="base64(HMAC-SHA1(secret, username)); expires with the username"
    )


class RtcIceConfig(BaseModel):
    iceServers: list[RtcIceServer] = Field(description="Pass as `RTCConfiguration.iceServers`")  # noqa: N815
    expires_at: int | None = Field(
        default=None, description="Unix time the TURN credentials stop working (None = no TURN REST)"
    )
    ttl_s: int | None = None
