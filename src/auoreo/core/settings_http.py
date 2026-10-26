"""G1a: HTTP control-plane hardening + webhook ingress knobs (spec §5.1, §19.11, §24.7).

Attached to `Settings` as `http: HttpSecuritySettings` and `webhooks: WebhookSettings`;
set them in `settings.json` (`{"http": {"trusted_hosts": ["box.lan"]}}`) or as JSON env vars
(`AUOREO_HTTP='{"queue_bound": 64}'`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HttpSecuritySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host_check: bool = True
    """Reject requests whose `Host` is not loopback, the bind host or `trusted_hosts` (400)."""
    trusted_hosts: list[str] = Field(default_factory=list)
    """Extra `Host` names (`box.lan`, `*.example.com`, `[::1]`, `*` = any) — e.g. a tunnel URL."""
    queue_bound: int = Field(default=128, ge=1, le=100_000)
    """Queued (not yet running) followups per session; overflow → 429 + `Retry-After`."""
    queue_retry_after_s: int = Field(default=2, ge=1, le=600)
    replay_cap: int = Field(default=10_000, ge=1, le=1_000_000)
    """Most events one `GET /api/events` replays; beyond it → 206 + `cap_warning`, then the
    stream ends so the client resumes from `Last-Event-ID`."""
    cookie_max_age_s: int = Field(default=30 * 86_400, ge=60)
    ws_auth_ttl_s: float | None = Field(default=None, gt=0)
    """Close `/ws` connections with 4001 (auth expired; client re-authenticates, ≤3 retries)
    this long after the handshake. None = never; a changed password always closes with 4401."""


class WebhookSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strict_secrets: bool = True
    """Fail closed (spec §19.11): refuse to start when an enabled route's secret is missing.
    `false` downgrades to a startup warning + 403 per request (local experiments only)."""
    rotation_grace_s: int = Field(default=86_400, ge=0)
    """How long `secret:webhook/<route>#prev` keeps verifying after `auoreo webhook rotate`."""
