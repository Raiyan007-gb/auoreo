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


class M7Agents(_Tolerant):
    """W3 agents and bots. Connector routing has no switch here: it is on when a connector's
    config has `routes` (docs/designs/m7-agents-bots.md, open question 4)."""

    memory_tenants: bool = False
    """Honour `memory_tenant` on agent manifests."""
    citations: bool = False
    """Structured `citations[]` on replies."""
    strict_origins: bool = False
    """A suggestion proposed from a connector, job, webhook or kanban worker session never runs
    in that session (docs/designs/m7-agents-bots.md, Wave 2). Required by the flags below."""
    post_consent: bool = False
    """Connectors that consume `consent:post` (Mastodon) post only for an accepted suggestion."""
    post_daily_cap: int = Field(default=10, ge=1, le=1000)
    """Consent-gated posts per plugin per UTC day."""
    voice_inbound: bool = False
    """The `voice.transcribe` host request: connector voice notes become `[voice] ` text."""
    voice_max_seconds: int = Field(default=120, ge=1, le=3600)
    """Longest voice note transcribed (seconds, measured after decoding)."""
    voice_daily_seconds: int = Field(default=600, ge=1, le=86400)
    """Transcribed seconds per plugin per UTC day."""
    voice_max_bytes: int = Field(default=5 * 2**20, ge=1024, le=20 * 2**20)
    """Largest voice file accepted by `voice.transcribe` (bytes)."""

    @model_validator(mode="after")
    def _consent_features_need_strict_origins(self) -> M7Agents:
        for flag in ("post_consent", "voice_inbound"):
            if getattr(self, flag) and not self.strict_origins:
                raise ValueError(f"m7.agents.{flag} requires m7.agents.strict_origins")
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
