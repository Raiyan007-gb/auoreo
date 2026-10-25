"""Wire models for the team blackboard and supervisor decisions (W1-B, spec L1024)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BoardEntry(BaseModel):
    """One blackboard key without its value (list view)."""

    team_id: str
    key: str
    producer: str = Field(description="Member name (or `api:<actor>`) that wrote the current version")
    version: int = Field(description="Starts at 1; every put bumps it")
    bytes: int = Field(description="UTF-8 size of the value (≤ 65536)")
    created_at: datetime
    updated_at: datetime


class BoardArtifact(BoardEntry):
    value: str


class BoardPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(
        max_length=65_536, description="≤ 64 KiB UTF-8; values that look like secrets are refused"
    )


class TeamDecision(BaseModel):
    """A supervisor's continue/spawn/finish decision (`team.decision` event)."""

    seq: int
    created_at: datetime
    team_id: str
    child: str
    outcome: str = Field(description="`new` for a spawn, else the child's status or error code")
    decision: Literal["continue", "spawn", "finish"]
    reason: str = ""
