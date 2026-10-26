"""I2: gateway hardening — key rotation/pools, budget ceilings, structured output, tool schema
shaping, OpenRouter lane, per-deployment Router knobs, context probing and empty-reply
classes (spec §10.2, §10.3, §20.2, §25.6, §25.7, §25.9, §25.14).

Attached to `Settings` as `i2: I2Settings`; set in `settings.json`
(`{"i2": {"budgets": {"provider_daily": {"openrouter": 5}}}}`) or as a JSON env var
(`AUOREO_I2='{"openrouter": {"privacy": "zdr"}}'`). Every default keeps the pre-I2 behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RoutingStrategy = Literal[
    "simple-shuffle",
    "least-busy",
    "usage-based-routing",
    "latency-based-routing",
    "cost-based-routing",
    "usage-based-routing-v2",
]


class BudgetSettings(BaseModel):
    """Daily USD ceilings from the usage ledger (UTC day). Empty maps = no ceiling."""

    model_config = ConfigDict(extra="forbid")
    per_key_daily: dict[str, float] = Field(default_factory=dict)
    """Deployment id (`openrouter/anthropic/claude-sonnet-4.5`) or key-pool slot
    (`provider/openrouter/work`) → USD per day."""
    provider_daily: dict[str, float] = Field(default_factory=dict)
    """LiteLLM provider prefix (`openrouter`, `anthropic`) → USD per day."""
    tag_daily: dict[str, float] = Field(default_factory=dict)
    """Request tag (`metadata.tags`) → USD per day."""


class DeploymentTuning(BaseModel):
    """Per-deployment Router knobs (keyed by the deployment id in `models.<group>.models`)."""

    model_config = ConfigDict(extra="forbid")
    max_parallel_requests: int | None = Field(default=None, ge=1)
    base_model: str | None = None
    """Pricing/capability lookup name (`model_info.base_model`), e.g. for Azure deployments."""
    allowed_fails: int | None = Field(default=None, ge=0)
    cooldown: float | None = Field(default=None, ge=0)
    """Seconds this deployment cools down after `allowed_fails` (`model_info.cooldown_time`)."""
    order: int | None = Field(default=None, ge=1)
    """Overrides the position-derived order tier (1 primary → 2 on retryable failure)."""
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class OpenRouterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sort: Literal["price", "throughput", "latency"] | None = None
    """`provider.sort` (with `partition: none`); None = OpenRouter's default load balancing."""
    require_parameters: Literal["auto", "always", "never"] = "auto"
    """`auto`: send `require_parameters: true` when the request has tools or `response_format`."""
    privacy: Literal["default", "deny_collection", "zdr"] = "default"
    """`deny_collection` → `data_collection: deny`; `zdr` → that plus `zdr: true` (smaller pool)."""
    allow_nitro: bool = False
    """`:nitro` SKUs are stripped from deployment ids unless this is on."""
    live_catalog: bool = False
    """Fetch `GET /api/v1/models` at startup (cached `catalog_ttl_s`, offline-safe)."""
    catalog_ttl_s: float = Field(default=3600.0, gt=0)
    prefer_fast_model: bool = False
    """Aux chains sort OpenRouter ids newest-first from the live catalog (exclusions applied)."""


class KeyPoolSettings(BaseModel):
    """`provider/<name>/<slot>` secrets form a per-provider key pool (spec §25.9)."""

    model_config = ConfigDict(extra="forbid")
    strategy: Literal["round_robin", "least_recently_failed"] = "round_robin"
    cooldown_s: float = Field(default=5.0, ge=0)
    """A slot that answered 401/429 is skipped this long (jittered ±20 %)."""


class ToolShaping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strict: bool = True
    """Mark function tools `strict: true` where the family supports it and the schema allows it."""
    max_enum: int = Field(default=64, ge=1)
    """Enums longer than this are clamped (and the rest named in the description)."""


class I2Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    budgets: BudgetSettings = Field(default_factory=BudgetSettings)
    deployments: dict[str, DeploymentTuning] = Field(default_factory=dict)
    group_strategy: dict[str, RoutingStrategy] = Field(default_factory=dict)
    """Routing group → LiteLLM routing strategy (others keep `simple-shuffle`)."""
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    keypool: KeyPoolSettings = Field(default_factory=KeyPoolSettings)
    tool_shaping: ToolShaping = Field(default_factory=ToolShaping)
    fallback_structured: bool = False
    """A model without `json_schema` support gets one aux retry instead of a FORMAT fail-fast."""
    probe_tiers: list[int] = Field(default_factory=lambda: [256_000, 128_000, 64_000, 32_000, 16_000, 8_000])
    probe_default: int = Field(default=128_000, ge=1)
    """Window assumed for a model no table knows (warned once, never persisted)."""
    min_context: int = Field(default=64_000, ge=0)
    """A known window below this is rejected up front (user-pinned `max_input_tokens` wins)."""
    thinking_prefill_retries: int = Field(default=2, ge=0, le=5)


__all__ = [
    "BudgetSettings",
    "DeploymentTuning",
    "I2Settings",
    "KeyPoolSettings",
    "OpenRouterSettings",
    "RoutingStrategy",
    "ToolShaping",
]
