"""I5 wire models: in-process LSP stop, the Memory page's usage/compression gauge."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LspStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="Only this session's servers.")
    language: str | None = Field(default=None, description="Only this language (python, rust, …).")


class LspStopped(BaseModel):
    stopped: list[str] = Field(description="`<session>:<language>:<root>` of every stopped server.")
    count: int


class MemoryUsage(BaseModel):
    """Long-term memory size plus how much context compaction has saved (`compaction.completed`)."""

    enabled: bool
    facts: int = 0
    chunks: int = 0
    transcript: int = 0
    archived_facts: int = 0
    vector: bool = False
    db_bytes: int = 0
    compactions: int = 0
    pre_tokens: int = 0
    """Sum of prompt tokens before each compaction."""
    post_tokens: int = 0
    """Sum of prompt tokens right after each compaction."""
    saved_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    """`1 - post/pre` over all compactions (0 when nothing was compacted)."""
    archived_messages: int = 0
