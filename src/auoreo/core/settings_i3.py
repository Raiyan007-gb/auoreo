"""I3: teams/scheduler closure — kanban dispatcher, cron manifest extras, exact fork, in-tool
staleness, handoff validation (spec §16.1–§16.7, §24.10, §25.7, §25.11, §25.15).

Attached to `Settings` as `i3: I3Settings`; set it in `settings.json`
(`{"i3": {"kanban": {"dispatch": true}}}`) or as a JSON env var (`AUOREO_I3='{...}'`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KanbanDispatchSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dispatch: bool = False
    """Run the kanban dispatcher (spec §16.6): every `interval_s` it claims ready tasks on
    `boards` and spawns one worker session per task. Off by default (it starts agent runs)."""
    interval_s: float = Field(default=60.0, gt=0, le=3600)
    boards: list[str] = Field(default_factory=lambda: ["default"])
    agent: str = "implementer"
    """Default worker profile (agent folder) for a task without `profile`."""
    profile_caps: dict[str, int] = Field(default_factory=dict)
    """Max concurrently running workers per profile; unlisted profiles use `default_cap`."""
    default_cap: int = Field(default=2, ge=1, le=32)
    wip_per_worker: int = Field(default=3, ge=1, le=32)
    """A worker may hold at most this many claimed tasks (spec §25.11 board WIP≤3/worker)."""
    success_cooldown_s: float = Field(default=3600.0, ge=0)
    """No new worker for a task within this long after a successful run (respawn guard)."""
    quota_cooldown_s: float = Field(default=300.0, ge=0)
    """No new worker for a task within this long after a quota/rate-limit failure."""
    pr_cooldown_s: float = Field(default=86_400.0, ge=0)
    """No new worker for a PR task (a `/pull/<n>` attachment or a `PR` title) within 24 h."""
    log_max_bytes: int = Field(default=2 * 1024 * 1024, ge=4096)
    """Worker log rotation threshold (`<home>/kanban/logs/<board>/<worker>.log` → `.1`)."""
    run_timeout_s: float = Field(default=1800.0, ge=10)


class CronI3Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    script_timeout_s: float = Field(default=30.0, gt=0, le=600)
    """Hard timeout of a job's pre-run `script`."""
    script_max_chars: int = Field(default=16_000, ge=1_000, le=200_000)
    """Script stdout injected into the prompt is capped here (redacted first, spec §25.11)."""
    context_max_chars: int = Field(default=8_000, ge=500, le=100_000)
    """Cap of a `context_from` previous-run output block."""
    keep_runs: int = Field(default=1000, ge=10, le=100_000)
    """`job_runs` rows kept per job (older ones are pruned after each run)."""
    header: str = "[{job}] scheduled run {when}"
    """Delivery header (`{job}`, `{when}`, `{run_id}`); empty disables it."""
    footer: str = "— AUOREO cron · auoreo cron runs {job}"
    notify_linger_s: float = Field(default=3600.0, ge=1, le=86_400)
    """`notify_on_complete` background watchers give up after this long (bounded linger)."""


class DelegationI3Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stale_idle_s: float = Field(default=450.0, gt=0)
    """A child with no event for this long while NOT inside a tool call fails `STALE`."""
    stale_in_tool_s: float = Field(default=1200.0, gt=0)
    """…and this long while a tool call is running (tool.started without tool.completed)."""
    fork_cache_exact: bool = True
    """`mode: exact` forks inherit the parent's full history + byte-identical system prompt +
    tool list (provider prompt caching hits). False → `exact` degrades to the summary fork."""
    handoff_validation: bool = True
    """Validate a child's final answer against the handoff schema when the parent asked for
    it (`handoff: true`), with one retry (spec §25.11)."""


class I3Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kanban: KanbanDispatchSettings = Field(default_factory=KanbanDispatchSettings)
    cron: CronI3Settings = Field(default_factory=CronI3Settings)
    delegation: DelegationI3Settings = Field(default_factory=DelegationI3Settings)
