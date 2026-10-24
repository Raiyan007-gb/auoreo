"""WebSocket control-plane frames (spec §5.2). Durable server→client events reuse
`schema.events.ServerEvent` verbatim — WS never gets a second event model.

Client → server frames are discriminated on `type`. Every frame is one JSON object per
WS text message; readers tolerate `\\n`-joined batches, writers never produce them.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ client → server
class WsHello(_Frame):
    type: Literal["hello"]
    last_seq: int | None = Field(default=None, ge=0)
    channels: list[str] | None = None
    """`session:<id>` | `server`; None = everything."""


class WsSubscribe(_Frame):
    type: Literal["subscribe"]
    channels: list[str]
    last_seq: int | None = Field(default=None, ge=0)


class WsPong(_Frame):
    type: Literal["pong"]
    tick: int


class WsPermissionReply(_Frame):
    kind: Literal["permission"]
    reply: Literal["once", "always", "reject"]
    message: str | None = None


class WsQuestionAnswer(_Frame):
    kind: Literal["question"]
    responses: list[dict[str, Any]]


class WsControlResponse(_Frame):
    """Answer to a server-initiated request (`permission.requested` / `question.asked`)."""

    type: Literal["control_response"]
    request_id: str
    response: Annotated[WsPermissionReply | WsQuestionAnswer, Field(discriminator="kind")]


class WsInterrupt(_Frame):
    kind: Literal["interrupt"]
    session_id: str


class WsSteer(_Frame):
    kind: Literal["steer"]
    session_id: str
    text: str = Field(min_length=1)


class WsFollowup(_Frame):
    kind: Literal["followup"]
    session_id: str
    text: str = Field(min_length=1)
    agent: str | None = None
    model: str | None = None


class WsControlRequest(_Frame):
    """Client-initiated control. Advisory and idempotent; unknown ids are not errors."""

    type: Literal["control_request"]
    request_id: str
    request: Annotated[WsInterrupt | WsSteer | WsFollowup, Field(discriminator="kind")]


ClientFrame = Annotated[
    WsHello | WsSubscribe | WsPong | WsControlResponse | WsControlRequest,
    Field(discriminator="type"),
]


# ------------------------------------------------------------------ server → client
class WsConnected(_Frame):
    type: Literal["connected"]
    server_seq: int
    generation: int
    pending_permissions: list[dict[str, Any]]
    pending_questions: list[dict[str, Any]]


class WsPing(_Frame):
    type: Literal["ping"]
    tick: int
    server_seq: int


class WsAck(_Frame):
    type: Literal["ack"]
    request_id: str
    ok: bool
    detail: str | None = None


class WsError(_Frame):
    type: Literal["error"]
    subtype: Literal["unknown_type", "invalid_frame", "binary_dropped", "unauthorized", "not_found"]
    message: str
    request_id: str | None = None


# Close codes (normative, spec §5.3)
CLOSE_OK = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY = 1008  # auth failure before/after accept
CLOSE_KEEPALIVE = 1011
CLOSE_TRY_LATER = 1013  # backpressure shed; client replays from last seq
CLOSE_UNAUTHORIZED = 4003  # permanent, no retry
