"""H2: delegation contract, job hard interrupt, chat approvals and webhook ingress extras
(spec §16.1, §16.7, §19.9, §19.11).

Attached to `Settings` as `teams2: Teams2Settings`; set it in `settings.json`
(`{"teams2": {"delegation": {"pause": true}}}`) or as a JSON env var (`AUOREO_TEAMS2='{...}'`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DelegationSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pause: bool = False
    """`delegation.pause`: refuse new spawns (list/steer/stop still work). The API can flip it
    at runtime (`PUT /api/delegation/pause`)."""
    poll_s: float = Field(default=1.0, gt=0, le=60)
    """How often a running child is checked (live log, budget, staleness)."""
    heartbeat_s: float = Field(default=30.0, gt=0, le=3600)
    """`subagent.progress` cadence on the parent while a child runs."""
    stale_warn_s: float = Field(default=450.0, gt=0)
    """No child event for this long → one `subagent.progress` with `stale: "warn"`."""
    stale_fail_s: float = Field(default=1200.0, gt=0)
    """No child event for this long → the child is stopped and marked failed (`STALE`)."""
    live_logs: bool = True
    """Mirror child events to `<home>/sessions/<parent>/delegation/live/<child>.jsonl`."""
    fork_seed_max_chars: int = Field(default=32_000, ge=1_000, le=400_000)
    """Cap of the parent-context seed a `mode: fork` child starts with (~8k tokens)."""


class Teams2Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delegation: DelegationSettings = Field(default_factory=DelegationSettings)
    job_timeout_s: int = Field(default=180, ge=10, le=86_400)
    """Hard interrupt for scheduled jobs (spec §16.7: 3 min); a job's `timeout_s` overrides it."""
    chat_approvals: bool = False
    """Inbound connector chats run interactively: approval requests are sent to the chat and
    answered with `/approve`, `/deny [reason]`, `/approve all` by a paired owner identity."""
    webhook_trusted_proxies: list[str] = Field(default_factory=list)
    """CIDRs of reverse proxies whose `X-Forwarded-For` is honoured for `allowed_cidrs`."""
    webhook_script_timeout_s: float = Field(default=30.0, gt=0, le=300)
    """Default timeout of a webhook `script:` transform."""
