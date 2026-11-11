"""M7 settings: every knob of the M7 milestone, in one tolerant model.

`extra="ignore"` on purpose: a key written by a newer build is ignored by an older one, so a
rollback never fails `Settings()` (the models M7 extends, such as `AgentManifest`,
`SshEnvironment` or `GatewayTuning`, are `extra="forbid"` and must not gain M7 keys). Every
feature is off by default until it is measured; see `docs/designs/m7-*.md`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra="ignore")


class M7Token(_Tolerant):
    """W1 token economy."""

    telemetry: bool = False
    """Prefix/tool hashes and breakpoints on `usage` events; `cache.prefix_changed` warnings."""
    cache_layout: Literal["legacy", "tail"] = "legacy"
    """Anthropic breakpoints: `legacy` = last user message; `tail` = last message + previous turn."""
    context_carrier: Literal["splice", "persisted"] = "splice"
    """Per-turn context block: spliced at request time, or persisted with the turn message."""
    cached_pricing: bool = False
    """Price cached and cache-write tokens at their own rates."""
    openai_cache_key: bool = False
    """Send `prompt_cache_key = session_id` to OpenAI-family models."""
    read_dedupe: bool = False
    """A repeated, unchanged `read` in the same turn returns a short notice."""


class M7Ssh(_Tolerant):
    """W2 SSH hosts."""

    hosts: bool = False
    """The `hosts/<name>.yaml` inventory and the per-host `ssh` permission."""


class M7Triage(_Tolerant):
    """W3 wave 2: the `kanban_create` suggestion action (allowlists, per-source caps, dedupe)."""

    boards: list[str] = Field(default_factory=list)
    """Boards a `kanban_create` suggestion may name (empty: none)."""
    profiles: list[str] = Field(default_factory=list)
    """Worker agents such a task may run as (the given `profile`, else `i3.kanban.agent`)."""
    per_source_pending: int = Field(default=5, ge=1, le=100)
    """Pending `kanban_create` suggestions per source (`webhook:<route>`, `job:<name>`, …)."""
    per_source_daily: int = Field(default=20, ge=1, le=10_000)
    """`kanban_create` suggestions per source in the last 24 hours, any status."""
    dedupe_days: int = Field(default=30, ge=1, le=365)
    """A key proposed, accepted or denied within this many days is not proposed again."""


class M7Agents(_Tolerant):
    """W3 agents and bots. Connector routing has no switch here: it is on when a connector's
    config has `routes` (docs/designs/m7-agents-bots.md, open question 4)."""

    memory_tenants: bool = False
    """Honour `memory_tenant` on agent manifests."""
    citations: bool = False
    """Structured `citations[]` on replies."""
    strict_origins: bool = False
    """Connector, job, webhook and kanban-worker sessions are unattended: a suggestion they
    propose runs in a new session as the origin's own agent, never in the origin session, and
    a job, webhook or connector session cannot call `kanban_create` directly."""
    kanban_suggestions: bool = False
    """The `kanban_create` suggestion action (requires `strict_origins`)."""
    triage: M7Triage = Field(default_factory=M7Triage)
    post_consent: bool = False
    """Consent-gated posting for public connectors (requires `strict_origins`)."""
    voice_inbound: bool = False
    """Inbound voice notes from paired chats (requires `strict_origins`)."""

    @model_validator(mode="after")
    def _wider_flags_need_strict_origins(self) -> M7Agents:
        wider = [n for n in ("kanban_suggestions", "post_consent", "voice_inbound") if getattr(self, n)]
        if wider and not self.strict_origins:
            raise ValueError(
                f"m7.agents.{wider[0]} requires m7.agents.strict_origins (else an unattended "
                "session's suggestion could run in that session)"
            )
        return self


class M7Orch(_Tolerant):
    """W4 orchestration."""

    session_cwd: bool = False
    """Per-session workspace root."""
    worktrees: bool = False
    """`worktree: true` on delegate_task and the kanban dispatcher (requires `session_cwd`)."""
    max_worktrees: int = Field(default=8, ge=1, le=64)
    """Live orchestrator worktrees per repository; over the cap a spawn fails, nothing is evicted."""
    worktree_root: str | None = None
    """Where orchestrator worktrees go; default `<home>/worktrees/<repo>-<hash8>/` (outside the
    checkout), after `g6.review.worktree_root` when that is set."""

    @model_validator(mode="after")
    def _worktrees_need_a_session_cwd(self) -> M7Orch:
        if self.worktrees and not self.session_cwd:
            raise ValueError("m7.orch.worktrees requires m7.orch.session_cwd (else isolation is prompt-only)")
        return self


class M7Settings(_Tolerant):
    token: M7Token = Field(default_factory=M7Token)
    ssh: M7Ssh = Field(default_factory=M7Ssh)
    agents: M7Agents = Field(default_factory=M7Agents)
    orch: M7Orch = Field(default_factory=M7Orch)
