"""Wire models for `POST /api/livekit/token` and `POST /livekit/webhook` (spec §26.9/§26.10, I8).

The token request follows LiveKit's standard token-endpoint schema (what `TokenSource.endpoint`
sends) plus AUOREO's `session_id`. The participant identity is ALWAYS minted server-side as an
opaque uuid4 (never PII, never client-chosen); `room_name` must be a room this server created.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LiveKitAgentDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=64)
    metadata: str | None = Field(default=None, max_length=1024)


class LiveKitRoomConfig(BaseModel):
    """Only explicit agent dispatch is allowed; any other room option is refused (400)."""

    model_config = ConfigDict(extra="forbid")

    agents: list[LiveKitAgentDispatch] = Field(default_factory=list, max_length=4)


class LiveKitTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="AUOREO session the room belongs to")
    room_name: str | None = Field(
        default=None,
        max_length=128,
        description="An existing room from this server (default: the session's room)",
    )
    participant_identity: str | None = Field(
        default=None,
        max_length=128,
        deprecated=True,
        description="Ignored: the server mints an opaque uuid4 identity (spec §26.10)",
    )
    participant_name: str | None = Field(default=None, max_length=64)
    participant_metadata: str | None = Field(default=None, description="Token metadata (size-capped)")
    participant_attributes: dict[str, str] | None = Field(
        default=None, description="Token attributes (size-capped)"
    )
    room_config: LiveKitRoomConfig | None = None


class LiveKitToken(BaseModel):
    server_url: str
    participant_token: str = Field(description="Room-scoped JWT (never logged or audited)")
    room_name: str
    participant_identity: str
    expires_in: int = Field(description="Token lifetime in seconds (≤ 900)")


class LiveKitWebhookAck(BaseModel):
    status: str = Field(description="`ok` or `duplicate`")
    event: str | None = None
    id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
