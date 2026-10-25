"""Request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(_In):
    message: str = Field(min_length=1, max_length=1_000_000)
    session_id: str | None = None
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None
    mode: Literal["followup", "steer", "inject"] = "followup"
    request_id: str | None = Field(default=None, description="Idempotency key (UUID)")


class ChatAccepted(BaseModel):
    session_id: str
    run_id: str | None
    deduped: bool = False


class SessionCreate(_In):
    title: str | None = None
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None


class SessionInfo(BaseModel):
    id: str
    title: str
    agent: str
    workflow: str
    model: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    idx: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class PermissionReply(_In):
    reply: Literal["once", "always", "reject"]
    message: str | None = Field(default=None, max_length=4000)


class PendingPermission(BaseModel):
    request_id: str
    session_id: str
    tool: str
    permission: str
    pattern: str
    detail: str


class HarnessEntry(BaseModel):
    kind: str
    name: str
    source: str
    path: str | None
    description: str


class HarnessListing(BaseModel):
    generation: int
    entries: dict[str, list[HarnessEntry]]
    errors: list[str]


class ReloadResult(BaseModel):
    generation: int
    errors: list[str]
    changes: dict[str, dict[str, list[str]]]


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    generation: int
    gateway: str


class UsageRecord(BaseModel):
    id: int
    session_id: str
    run_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float | None
    estimated: bool
    created_at: datetime


class UsageTotals(BaseModel):
    key: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class UsageSummary(BaseModel):
    days: int
    total: UsageTotals
    by_model: list[UsageTotals]
    by_session: list[UsageTotals]


class CompactRequest(_In):
    retain_ratio: float | None = Field(default=None, ge=0, le=1)


class CompactionOut(BaseModel):
    compaction_id: str
    pre_tokens: int
    post_tokens: int
    archived: int
    pruned: int
    summary_chars: int


class ArchivedMessage(BaseModel):
    compaction_id: str
    idx: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    archived_at: datetime


class LatestSeq(BaseModel):
    seq: int


class SessionDetail(SessionInfo):
    """`SessionInfo` plus lineage/archive state (fork, archive endpoints)."""

    parent_id: str | None = None
    archived: bool = False


class SessionDeleted(BaseModel):
    id: str
    deleted: bool


class CommandInfo(BaseModel):
    name: str
    description: str
    handler: Literal["prompt", "compact", "model", "plan"]
    args_hint: str
