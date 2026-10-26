"""`settings.compaction` (spec §11.8, §11.9 S4/S5/S7, §11.6): G2b knobs, settings.json-only.

Kept in one nested model so `core.settings` only grows by the attachment line. Memory-side
knobs that the spec files under `memory.*` (`nudge_interval`, `rewrite_memory_query`) live
here as `memory_nudge_interval` / `rewrite_memory_query` for the same reason."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompactionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # S4(c) idle trigger
    idle_compact_after_seconds: float | None = Field(default=None, gt=0)
    """Compact a session that has been idle this long (None = off; spec example 1800)."""
    idle_min_tokens: int = Field(default=20_000, ge=0)
    """Idle compaction only runs when the history holds at least this many tokens."""

    # S5 summarize
    timeout_ladder_s: list[float] = Field(default_factory=lambda: [60.0, 300.0, 900.0])
    """Per-attempt summarizer timeouts; a timeout retries with the next rung."""
    ptl_max_retries: int = Field(default=3, ge=0, le=10)
    """Prompt-too-long retries, each dropping the oldest API-round group."""

    # S7 session-memory compaction + post-compaction restore
    session_memory: bool = True
    """Try the LLM-free session-memory compaction first (`<home>/sessions/<id>/session-memory.md`)."""
    session_memory_min_tokens: int = Field(default=10_000, ge=0)
    session_memory_min_messages: int = Field(default=5, ge=1)
    session_memory_max_tokens: int = Field(default=40_000, ge=1)
    restore_files: int = Field(default=5, ge=0, le=20)
    restore_file_chars: int = Field(default=5_000, ge=0)
    restore_files_total_chars: int = Field(default=50_000, ge=0)
    restore_skill_chars: int = Field(default=5_000, ge=0)
    restore_skills_total_chars: int = Field(default=25_000, ge=0)
    metadata_chars: int = Field(default=16_384, ge=0)
    rerun_session_start: bool = True

    # §11.8 / §11.6 memory helpers
    memory_nudge_interval: int = Field(default=10, ge=0)
    """Every N user turns remind the agent to save durable facts (0 = off)."""
    rewrite_memory_query: bool = False
    """One aux call (temperature 0, ≤96 tokens) rewrites the recall query; fail-open."""

    @field_validator("timeout_ladder_s")
    @classmethod
    def _ladder(cls, v: list[float]) -> list[float]:
        if not v or any(t <= 0 for t in v):
            raise ValueError("timeout_ladder_s needs at least one positive timeout")
        return v


__all__ = ["CompactionSettings"]
