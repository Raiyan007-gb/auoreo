"""H2 wire models: job creation (`POST /api/cron`), the delegation pause switch and children."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CronCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    schedule: str = Field(
        min_length=1,
        max_length=200,
        description="cron | every <n>m|h|d | <n>m|h|d | every monday 9am [tz] | in <n>m|h|d | ISO datetime",
    )
    prompt: str = Field(min_length=1, max_length=20_000)
    description: str | None = Field(default=None, max_length=500)
    tz: str | None = Field(default=None, max_length=64)
    agent: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]*$")
    deliver: list[str] = Field(default_factory=lambda: ["log"], max_length=5)
    timeout_s: int | None = Field(default=None, ge=10, le=86_400)
    enabled: bool = True
    catch_up: bool = True
    dry_run: bool = Field(default=False, description="Validate and preview; nothing is saved")
    # --- I3 (spec §16.7)
    model: str | None = Field(default=None, max_length=255)
    provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    script: str | None = Field(
        default=None, max_length=255, description="Pre-run script under <home>/scripts/"
    )
    context_from: list[str] = Field(default_factory=list, max_length=5, description="previous | <job name>")
    workdir: str | None = Field(default=None, max_length=1024)
    header: str | None = Field(default=None, max_length=500, description='Delivery header ("" disables)')
    footer: str | None = Field(default=None, max_length=500, description='Delivery footer ("" disables)')


class CronPlan(BaseModel):
    name: str
    schedule: str
    kind: str = Field(description="cron | interval | date")
    tz: str
    next_fires: list[str] = Field(description="Next (up to) 5 fire times, ISO-8601")
    catch_up: str = Field(description="Catch-up class for a missed fire")
    dry_run: bool
    created: bool


class DelegationState(BaseModel):
    paused: bool = Field(description="True: new spawns are refused (list/steer/stop still work)")
    source: str = Field(description="settings | api")
    running: int = Field(description="Children running right now (process-wide)")
    queued: int = Field(description="Children waiting for a slot")


class DelegationPause(BaseModel):
    paused: bool


class SubagentInfo(BaseModel):
    subagent_id: str = Field(description="sa-{n}-{hex8} (I3); the child session id for older rows")
    task_id: str = Field(default="", description="The child session id")
    name: str
    agent: str
    depth: int
    role: str | None
    status: str
    running: bool
    usage: dict[str, Any] = Field(default_factory=dict)
