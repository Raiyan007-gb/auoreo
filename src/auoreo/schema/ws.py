"""WebSocket control-plane frames (spec §5.2). Durable server→client events reuse
`schema.events.ServerEvent` verbatim — WS never gets a second event model.

Client → server frames are discriminated on `type`. Every frame is one JSON object per
WS text message; readers tolerate `\\n`-joined batches and JSON arrays of ≤32 frames
(JSON-RPC style; more → `invalid_frame` with code -32600), writers never produce them.
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


class WsUnsubscribe(_Frame):
    type: Literal["unsubscribe"]
    channels: list[str] = Field(min_length=1)


class WsPong(_Frame):
    type: Literal["pong"]
    tick: int


class WsClientPing(_Frame):
    """Client keepalive; answered at once with `pong{tick}` (ahead of queued events)."""

    type: Literal["ping"]
    tick: int = 0


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


class WsEdit(_Frame):
    """Replace the text of a queued, not-yet-run followup. `target` is the followup's
    `request_id` (or its run id, from the ack)."""

    kind: Literal["edit"]
    session_id: str
    target: str = Field(min_length=1)
    text: str = Field(min_length=1)


class WsControlRequest(_Frame):
    """Client-initiated control. Advisory and idempotent; unknown ids are not errors."""

    type: Literal["control_request"]
    request_id: str
    request: Annotated[WsInterrupt | WsSteer | WsFollowup | WsEdit, Field(discriminator="kind")]


ClientFrame = Annotated[
    WsHello | WsSubscribe | WsUnsubscribe | WsPong | WsClientPing | WsControlResponse | WsControlRequest,
    Field(discriminator="type"),
]
MAX_BATCH = 32
"""Most frames in one JSON-array batch."""


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


class WsServerPong(_Frame):
    type: Literal["pong"]
    tick: int
    server_seq: int


class WsSubscribed(_Frame):
    type: Literal["subscribed"]
    channels: list[str] | None
    """The connection's channels after the change; None = everything."""
    server_seq: int


class WsUnsubscribed(_Frame):
    type: Literal["unsubscribed"]
    channels: list[str]
    """Removed by this frame."""
    remaining: list[str]


class WsAck(_Frame):
    type: Literal["ack"]
    request_id: str
    ok: bool
    detail: str | None = None


class WsError(_Frame):
    type: Literal["error"]
    subtype: Literal[
        "unknown_type", "invalid_frame", "binary_dropped", "unauthorized", "not_found", "unknown_channel"
    ]
    message: str
    request_id: str | None = None
    code: int | None = None
    """JSON-RPC style code where one applies (-32600 invalid request / oversized batch)."""


# Close codes (normative, spec §5.3)
CLOSE_OK = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY = 1008  # auth failure before/after accept
CLOSE_KEEPALIVE = 1011
CLOSE_TRY_LATER = 1013  # backpressure shed; client replays from last seq
CLOSE_AUTH_EXPIRED = 4001  # transient: credentials expired / session gone; client may retry ≤3
CLOSE_UNAUTHORIZED = 4003  # permanent, no retry
CLOSE_REVOKED = 4401  # credentials revoked after the handshake; terminal
