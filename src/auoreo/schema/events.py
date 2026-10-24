"""The single event envelope used by SSE (and later WS). Pydantic models are the wire contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Event types that may be dropped/coalesced under backpressure (spec §5.3).
COALESCABLE = frozenset({"message.delta", "tool.progress", "ping"})


class ServerEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(description="str(seq) — use as SSE id / Last-Event-ID")
    seq: int = Field(description="Global monotonic sequence (DB autoincrement)")
    type: str = Field(description="e.g. run.started, message.delta, tool.completed")
    aggregate_id: str = Field(description="Session id, or 'server'")
    aggregate_seq: int = Field(description="Monotonic sequence within the aggregate")
    data: dict[str, Any]
    created_at: datetime
