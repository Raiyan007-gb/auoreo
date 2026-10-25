"""`settings.bandit` (spec §12.5 / §24.4): contextual epsilon-greedy over model × effort × top_k.

Pure pydantic, no auoreo imports, so `core.settings` can nest it without a cycle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Effort = Literal["low", "medium", "high", "max"]


class Arm(BaseModel):
    """One choice the bandit can make for a turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str
    effort: Effort | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    """Recorded with the arm; memory recall has no per-call limit yet, so it is not applied."""

    @property
    def key(self) -> str:
        return f"{self.model}|{self.effort or '-'}|{self.top_k or '-'}"


class BanditWeights(BaseModel):
    """Reward = success·w − cost·w − latency·w + cache_hit·w − fallbacks·w − corrections·w."""

    model_config = ConfigDict(extra="forbid")
    success: float = 1.0
    cost: float = 0.3
    latency: float = 0.2
    cache_hit: float = 0.1
    fallbacks: float = 0.2
    corrections: float = 0.3


class BanditSettings(BaseModel):
    """Default OFF. `enabled: false` is the kill switch: it takes effect on the next turn
    boundary (a running turn keeps the arm it started with, and records nothing)."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    epsilon: float = Field(default=0.15, ge=0, le=1)
    epsilon_decay: float = Field(default=0.02, ge=0, le=1)
    """Subtracted from `epsilon` per 100 pulls in a context."""
    min_epsilon: float = Field(default=0.02, ge=0, le=1)
    arms: list[Arm] = Field(default_factory=list)
    """Empty → the `default` and `small` routing groups × effort {none, high}."""
    weights: BanditWeights = Field(default_factory=BanditWeights)
    contexts: Literal["agent", "agent+size"] = "agent"
    """`agent+size` also buckets by prompt length (s < 500 chars ≤ m < 4000 ≤ l)."""


__all__ = ["Arm", "BanditSettings", "BanditWeights", "Effort"]
