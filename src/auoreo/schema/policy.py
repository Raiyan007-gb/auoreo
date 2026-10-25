"""Wire models for the model/effort bandit (W1-C, spec §12.5): `/api/policy/bandit`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Effort = Literal["low", "medium", "high", "max"]


class BanditArmInfo(BaseModel):
    """One configured arm (`settings.bandit.arms`, or derived from the routing groups)."""

    key: str
    model: str
    effort: Effort | None = None
    top_k: int | None = None


class BanditArmStats(BanditArmInfo):
    context_key: str
    pulls: int
    mean_reward: float
    updated_at: datetime


class BanditContextInfo(BaseModel):
    context_key: str
    pulls: int
    epsilon: float = Field(description="Exploration rate the next choice in this context uses.")


class BanditOutcomeInfo(BaseModel):
    id: int
    session_id: str
    run_id: str
    context_key: str
    arm_key: str
    reward: float
    signals: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BanditStatus(BaseModel):
    enabled: bool
    epsilon: float
    epsilon_decay: float
    min_epsilon: float
    contexts_mode: Literal["agent", "agent+size"]
    weights: dict[str, float]
    arms: list[BanditArmInfo]
    contexts: list[BanditContextInfo]
    stats: list[BanditArmStats]
    recent: list[BanditOutcomeInfo]


class BanditResetRequest(BaseModel):
    context: str | None = Field(default=None, description="Only this context key; omit for all.")


class BanditResetResult(BaseModel):
    arms: int
    outcomes: int


class PolicyChoiceData(BaseModel):
    """Payload of the `policy.choice` session event (emitted before `run.started`)."""

    run_id: str
    context_key: str
    arm: BanditArmInfo
    explore: bool
    why: str
    epsilon: float


__all__ = [
    "BanditArmInfo",
    "BanditArmStats",
    "BanditContextInfo",
    "BanditOutcomeInfo",
    "BanditResetRequest",
    "BanditResetResult",
    "BanditStatus",
    "PolicyChoiceData",
]
