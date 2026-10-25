"""`POST /api/events:tail` — the latest seq plus a short tail (spec §25.12)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from auoreo.schema.events import ServerEvent


class EventsTailRequest(BaseModel):
    after_seq: int | None = Field(default=None, ge=0)
    """Events after this seq; None = the most recent `limit` events."""
    limit: int = Field(default=50, ge=1, le=500)
    channels: list[str] | None = None
    """`session:<id>` | `job:<id>` | `server`; None = everything."""


class EventsTail(BaseModel):
    seq: int
    """Highest event seq at the time of the call."""
    events: list[ServerEvent]
