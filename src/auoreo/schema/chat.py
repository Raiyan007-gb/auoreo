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
