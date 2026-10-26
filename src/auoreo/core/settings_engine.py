"""G4a: tool-engine knobs (spec §9.2–§9.6, §24.2, §26.14).

Attached to `Settings` as `tool_engine: ToolEngineSettings`; set it in `settings.json`
(`{"tool_engine": {"error_on_collision": true}}`) or as a JSON env var
(`AUOREO_TOOL_ENGINE='{"spill_max_bytes": 1048576}'`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SPILL_MAX_BYTES = 64 * 1024 * 1024


class ToolEngineSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error_on_collision: bool = False
    """Two roots (or an MCP server and a builtin) defining the same tool name without
    `override: true` is a hard error (dev/CI) instead of first-wins with a warning (prod)."""
    check_fn_ttl_s: float = Field(default=30.0, ge=0, le=3600)
    """How long a tool's `check_fn` availability verdict is cached."""
    spill_max_bytes: int = Field(default=SPILL_MAX_BYTES, ge=1024)
    """Largest spill file a tool result writes to `sessions/<id>/tool-results/`; the rest
    is cut and the result says so."""
    progress_interval_ms: int = Field(default=500, ge=0, le=60_000)
    """Minimum gap between two `tool.progress` events of one call (final/100% always sent)."""
    sampling_rpm: int = Field(default=10, ge=1, le=10_000)
    """Most MCP `sampling/createMessage` requests per server per minute (servers opt in)."""
