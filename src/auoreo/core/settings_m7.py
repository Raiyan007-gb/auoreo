"""M7 settings: every knob of the M7 milestone, in one tolerant model.

`extra="ignore"` on purpose: a key written by a newer build is ignored by an older one, so a
rollback never fails `Settings()` (the models M7 extends, such as `AgentManifest`,
`SshEnvironment` or `GatewayTuning`, are `extra="forbid"` and must not gain M7 keys). Every
feature is off by default until it is measured; see `docs/designs/m7-*.md`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    """W3 agents and bots."""

    memory_tenants: bool = False
    """Honour `memory_tenant` on agent manifests."""
    connector_routes: bool = False
    """Route inbound connector messages to agents by rule."""
    citations: bool = False
    """Structured `citations[]` on replies."""


class M7Orch(_Tolerant):
    """W4 orchestration."""

    session_cwd: bool = False
    """Per-session workspace root."""
    worktrees: bool = False
    """`worktree: true` on delegate_task and the kanban dispatcher."""


class M7Settings(_Tolerant):
    token: M7Token = Field(default_factory=M7Token)
    ssh: M7Ssh = Field(default_factory=M7Ssh)
    agents: M7Agents = Field(default_factory=M7Agents)
    orch: M7Orch = Field(default_factory=M7Orch)
