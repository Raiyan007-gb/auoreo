"""`jobs/<name>.yaml` manifest (spec §16.7, §25.7, §25.11), split out of `manifests.py`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

NAME_RE = r"^[a-z][a-z0-9_]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JobManifest(_Strict):
    """`jobs/<name>.yaml`. APScheduler only fires the trigger; AUOREO runs the job in its own
    session and records `job_runs` + `deliveries`."""

    name: str = Field(pattern=NAME_RE)
    description: str = Field(min_length=1)
    schedule: str = Field(min_length=1)
    """5-field cron (`0 9 * * 1-5`), `every 30m|2h|1d`, a bare duration (`30m` = every 30m),
    `every monday 9am [<tz>]`, or an ISO-8601 one-shot datetime."""
    tz: str | None = None
    """IANA zone for cron schedules; default = process zone."""
    agent: str = Field(default="default", pattern=NAME_RE)
    workflow: str | None = None
    prompt: str = Field(min_length=1)
    """Template; `{{now}}` and `{{job}}` are available."""
    skills: list[str] = Field(default_factory=list)
    deliver: list[str] = Field(default_factory=lambda: ["log"])
    """Delivery targets, tried in order (first success wins): `backends/delivery/<name>[:target]`
    or an alias — `origin` (where the job was created), `home` (`suggestions_home`), `all`
    (fan-out to every healthy provider)."""
    enabled: bool = True
    catch_up: bool = True
    """Fire once if a run was missed while the server was down (within half a period, max 2h)."""
    timeout_s: int | None = Field(default=None, ge=10, le=86_400)
    """Hard interrupt per run; default `teams2.job_timeout_s` (spec §16.7: 3 minutes)."""
    max_steps: int | None = Field(default=None, ge=1, le=500)
    # --- I3 (spec §16.7, §25.7, §25.11)
    model: str | None = Field(default=None, max_length=255)
    """Model alias/id for the run (`provider` prefixes it when it has no `/`)."""
    provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    script: str | None = Field(default=None, max_length=255)
    """Pre-run script under `<home>/scripts/`; its stdout (sandboxed read-only, redacted, capped,
    wrapped as UNTRUSTED data) is injected before the prompt."""
    context_from: list[str] = Field(default_factory=list)
    """`previous` (this job's last output) and/or other job names whose last output is injected."""
    workdir: str | None = Field(default=None, max_length=1024)
    """Working directory of the script (and the run's context note); must exist."""
    origin: str | None = Field(default=None, max_length=200)
    """Delivery spec `deliver: [origin]` resolves to (set by the cronjob tool from its session)."""
    origin_session: str | None = Field(default=None, max_length=64)
    notify_on_complete: bool = False
    """Inject a `<job-notification>` into `origin_session` when a run finishes (§25.7)."""
    header: str | None = Field(default=None, max_length=500)
    """Delivery header override (`{job}`, `{when}`, `{run_id}`); "" disables; None = settings."""
    footer: str | None = Field(default=None, max_length=500)
