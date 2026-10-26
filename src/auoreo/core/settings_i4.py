"""`settings.i4` — I4 knobs (spec §11.1–§11.9, §24.3, §25.8, §9.3), settings.json-only.

One nested model so `core.settings` only grows by its attachment line:
- `compaction` — S0 absolute threshold / buffer / per-model overrides / profile floor,
                 85% gateway hygiene with a turn hold, session-memory writer, S2 skill prune;
- `memory`     — re-embed job, injection excerpt, scopes/tenants, decay, contradiction scan,
                 CJK trigram FTS, docs-tier chunking, memory tool failure cap.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelCompactionOverride(BaseModel):
    """Per-model S0 override, matched by substring of the model id (longest match wins)."""

    model_config = ConfigDict(extra="forbid")
    threshold_ratio: float | None = Field(default=None, gt=0, le=1)
    threshold_tokens: int | None = Field(default=None, ge=1)


class CompactionI4Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_tokens: int | None = Field(default=None, ge=1)
    """Absolute auto-compaction threshold; whichever of ratio/tokens fires first wins."""
    buffer_tokens: int = Field(default=20_000, ge=0)
    """`warn = auto - buffer`: crossing it emits one `compaction.warning` per session."""
    model_overrides: dict[str, ModelCompactionOverride] = Field(default_factory=dict)
    profile_threshold_ratio: float | None = Field(default=None, gt=0, le=1)
    """A profile default ratio (e.g. 0.50); raised to `profile_floor` when window < 512K."""
    profile_floor: float = Field(default=0.75, gt=0, le=1)
    hygiene_ratio: float = Field(default=0.85, gt=0, le=1)
    """Gateway-side hygiene: compact before sending when the request is ≥ this × window."""
    hygiene_hold_s: float = Field(default=10.0, ge=0)
    """How long a turn waits for an in-flight compaction before sending anyway."""
    session_memory_writer: bool = True
    """Maintain `<home>/sessions/<id>/session-memory.md` at turn end (deterministic)."""
    session_memory_chars: int = Field(default=12_000, ge=1_000)
    skill_prune_min_chars: int = Field(default=5_000, ge=0)
    skill_prune_cap: int = Field(default=20, ge=0)


class MemoryI4Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reembed_auto: bool = True
    """Start the resumable re-embed job at startup when `memory.embed_model` changed."""
    reembed_batch: int = Field(default=128, ge=1, le=4096)
    reembed_throttle_s: float = Field(default=0.05, ge=0)
    injection_chars: int = Field(default=2_000, ge=200)
    """Combined MEMORY + USER (+ PREFS) excerpt cap in the per-turn context."""
    default_scope: Literal["session", "project", "user", "team"] = "user"
    decay: bool = True
    """Apply per-fact `half_life_days` decay in ranking (facts without it never decay)."""
    contradiction_scan: int = Field(default=500, ge=1)
    entity_overlap: float = Field(default=0.3, ge=0, le=1)
    cjk_trigram: bool = True
    docs_chunk_tokens: int = Field(default=900, ge=64)
    docs_overlap_ratio: float = Field(default=0.15, ge=0, le=0.5)
    tool_failures_per_turn: int = Field(default=3, ge=1)


class I4Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compaction: CompactionI4Settings = Field(default_factory=CompactionI4Settings)
    memory: MemoryI4Settings = Field(default_factory=MemoryI4Settings)


__all__ = [
    "CompactionI4Settings",
    "I4Settings",
    "MemoryI4Settings",
    "ModelCompactionOverride",
]
