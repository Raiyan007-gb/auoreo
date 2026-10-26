"""G5a wire models: rename, share links, retry and revert (spec §18 `auoreo session …`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from auoreo.schema.chat import SessionDetail


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRename(_In):
    title: str = Field(min_length=1, max_length=255)


class ShareCreated(BaseModel):
    session_id: str
    token: str
    """Shown once; the server keeps only its SHA-256."""
    path: str
    url: str


class SharesRevoked(BaseModel):
    session_id: str
    revoked: int


class SharedSession(BaseModel):
    """Read-only view behind a share token; secrets in messages are always redacted."""

    session: SessionDetail
    messages: list[dict[str, Any]]


class RevertRequest(_In):
    to_idx: int = Field(ge=-1, description="Keep messages 0..to_idx (-1 clears the history)")


class RevertResult(BaseModel):
    session_id: str
    to_idx: int
    removed: int


class RetryAccepted(BaseModel):
    session_id: str
    run_id: str | None
    text: str
