"""Gateway settings (spec §10.4, §10.7, §24.4): aux routing and Router/request tuning.

Attached to `Settings` as `auxiliary` and `gateway_tuning`; both are settings.json-only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AuxTask = Literal[
    "title",
    "compaction",
    "guardian",
    "hyde",
    "translate",
    "summary",
    "vision",
    "curator",
    "sampling",
    "memory_query",
]


class AuxSettings(BaseModel):
    """`settings.auxiliary`: which model serves background/aux calls (spec §10.7).

    Resolution (see `auoreo.gateway.auxiliary.resolve_aux_model`): `tasks[<task>]` → first entry of
    `text_chain` (or `vision_chain`) whose credentials exist → `small` group → main model.
    Entries are routing groups, `<provider>/<model>` ids, or `main` (the session's model)."""

    model_config = ConfigDict(extra="forbid")
    text_chain: list[str] = Field(default_factory=list)
    vision_chain: list[str] = Field(default_factory=list)
    free_only: bool = False
    """Force `:free` SKUs for OpenRouter ids in the chains."""
    tasks: dict[AuxTask, str] = Field(default_factory=dict)
    aux_probe_mode: Literal["off", "lazy"] = "lazy"
    """`lazy`: skip chain entries whose last call failed within `probe_ttl_s` (in-memory)."""
    probe_ttl_s: float = Field(default=300.0, ge=0)


class GatewayTuning(BaseModel):
    """`settings.gateway_tuning`: Router options and request shaping knobs (spec §10.3-§10.8)."""

    model_config = ConfigDict(extra="forbid")
    content_policy_fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    """Group → groups tried on CONTENT_POLICY (separate from `fallbacks`)."""
    weights: dict[str, list[float]] = Field(default_factory=dict)
    """Group → per-deployment weights (same order as `models.<group>.models`); any weight
    turns on LiteLLM's `enable_weighted_failover`."""
    session_affinity: bool = True
    """Pin a session to one deployment (`optional_pre_call_checks: [session_affinity]`)."""
    prompt_caching: bool = True
    """Anthropic-family `cache_control` breakpoints (tools | system | last user message)."""
    cache_ttl: Literal["5m", "1h"] = "5m"
    max_tokens_floor: int = Field(default=3000, ge=1)
    overflow_buffer: int = Field(default=1000, ge=0)
    stream_idle_timeout_s: float = Field(default=300.0, gt=0)
    overload_fallback_after: int = Field(default=3, ge=1)
    """Consecutive 529/overloaded errors on a foreground turn before falling back a group."""
    openrouter_referer: str = "https://github.com/auoreo/auoreo"
    openrouter_title: str = "AUOREO"


__all__ = ["AuxSettings", "AuxTask", "GatewayTuning"]
