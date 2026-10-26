"""Wire models for the session plan, per-session source toggles and usage charts (§2.3, §17)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="allow")
    step: str
    status: Literal["pending", "in_progress", "completed"] = "pending"
    done_when: str | None = None


class SessionPlan(BaseModel):
    """The newest `update_plan` steps of a session (from `plan.updated` events)."""

    steps: list[PlanStep]
    explanation: str = ""
    plan_mode: bool = False


class SessionSource(BaseModel):
    id: str = Field(description="`mcp:<server>` | `plugin:<name>`")
    kind: Literal["mcp", "plugin"]
    name: str
    enabled: bool = Field(description="Enabled for this session (sources are on unless disabled here)")
    status: str = Field(description="Global state: connected/running/disabled/…")
    tools: int = 0
    skills: list[str] = Field(default_factory=list)


class SessionSources(BaseModel):
    session_id: str
    sources: list[SessionSource]
    disabled: list[str]


class SessionSourcesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: list[str] = Field(description="Sources switched off for this session (replaces the list)")


class UsagePoint(BaseModel):
    bucket: datetime = Field(description="Start of the bucket (UTC)")
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class UsageBudget(BaseModel):
    """Budget guards from settings: per-session spend cap and per-turn limits."""

    max_cost_usd: float | None = None
    token_budget: int | None = None
    run_budget_seconds: float | None = None


class SessionSpend(BaseModel):
    session_id: str
    cost_usd: float
    ratio: float | None = Field(default=None, description="cost / max_cost_usd (None without a cap)")
    blocked: bool = False


class UsageSeries(BaseModel):
    days: int
    bucket: Literal["hour", "day"]
    points: list[UsagePoint]
    budget: UsageBudget
    sessions: list[SessionSpend] = Field(default_factory=list, description="Top spenders in range")
