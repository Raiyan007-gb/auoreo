"""Wire models for goals and proactive suggestions (W0-C): `/api/goals`, `/api/suggestions`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GoalStatus = Literal["active", "paused", "done", "dropped", "absorbed"]
SuggestionStatus = Literal["pending", "accepted", "denied", "expired", "tombstoned"]


class GoalInfo(BaseModel):
    id: int
    title: str
    description: str = ""
    status: GoalStatus
    progress: float = Field(ge=0, le=1)
    evidence_session_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    absorbed_into: int | None = None
    created_at: datetime
    updated_at: datetime


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)


class GoalPatch(BaseModel):
    status: Literal["active", "paused", "done", "dropped"] | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    evidence_session_id: str | None = Field(default=None, max_length=64, description="Append to evidence")


class GoalAbsorb(BaseModel):
    into: int = Field(ge=1, description="Goal that absorbs this one (its evidence is merged in)")


class SuggestionInfo(BaseModel):
    id: int
    kind: str
    title: str
    body: str = ""
    status: SuggestionStatus
    deliver: str = Field(description="origin | home | all | <provider>[:<target>]")
    origin_session: str | None = None
    action: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None
    delivery_id: int | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class SuggestionCreate(BaseModel):
    """Propose a suggestion (integrations, tests). It is still only executed on accept."""

    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=4000)
    kind: str = Field(default="info", max_length=32)
    deliver: str = Field(default="origin", max_length=64)
    origin_session: str | None = Field(default=None, max_length=64)
    action: dict[str, Any] = Field(
        default_factory=dict, description='e.g. {"type": "prompt", "agent", "text"}'
    )
    dedupe_key: str | None = Field(default=None, max_length=128)
    ttl_s: int = Field(default=86400, ge=1, le=2_592_000)


class SuggestionProposed(BaseModel):
    suggestion: SuggestionInfo
    created: bool = Field(description="False when a pending suggestion with the same dedupe_key existed")


class SuggestionAccepted(BaseModel):
    suggestion: SuggestionInfo
    session_id: str | None = Field(default=None, description="Session the accepted prompt runs in")
    run_id: str | None = None
