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


class M7Mask(_Tolerant):
    """W1 Wave 2 observation masking (`m7.token.masking`) and the report's mask gates."""

    keep_recent: int = Field(default=10, ge=0)
    """The last N tool results are never masked."""
    min_chars: int = Field(default=2000, ge=1)
    """Only string tool results of at least this many characters are masked."""
    min_tokens: int = Field(default=8000, ge=0)
    """Report gate M3: the maskable size from which a session counts as a masking candidate."""
    cache_read_multiplier: float = Field(default=0.1, ge=0)
    """Cache-read price / input price used by report gate M3 (Anthropic default; some models differ)."""
    cache_write_multiplier: float | None = Field(default=None, ge=0)
    """Cache-write price / input price for gate M3; None = 1.25 (`cache_ttl` 5m) or 2.0 (1h)."""


class M7Token(_Tolerant):
    """W1 token economy."""

    telemetry: bool = False
    """Prefix/tool hashes and breakpoints on `usage` events; `cache.prefix_changed` warnings."""
    cache_layout: Literal["legacy", "tail"] = "legacy"
    """Anthropic breakpoints: `legacy` = last user message; `tail` = the last user message plus the
    last markable message (the tail). There is no previous-turn breakpoint (Wave 2: not built)."""
    context_carrier: Literal["splice", "persisted"] = "splice"
    """Per-turn context block: spliced at request time, or persisted with the turn message."""
    cached_pricing: bool = False
    """Price cached and cache-write tokens at their own rates."""
    openai_cache_key: bool = False
    """Send `prompt_cache_key = session_id` to OpenAI-family models."""
    read_dedupe: bool = False
    """A repeated, unchanged `read` in the same turn returns a short notice."""
    mask: M7Mask = Field(default_factory=M7Mask)
    masking: Literal["off", "compaction"] = "off"
    """`compaction`: every compaction run first replaces old bulky tool results by placeholders
    (originals spilled under `sessions/<id>/tool-results/masked/`); `/compact unmask=` restores."""
    bandit_cache_signal: Literal["binary", "ratio"] = "binary"
    """Bandit reward cache term: `binary` (any cached token) or `ratio` (cached / prompt over
    main-turn calls). `cache_ratio` is always recorded in the outcome signals; switch only after
    report gate C1 passes, and keep `bandit.weights.cache_hit = 0` with `cached_pricing` on."""


class M7Ssh(_Tolerant):
    """W2 SSH hosts."""

    hosts: bool = False
    """The `hosts/<name>.yaml` inventory and the per-host `ssh` permission."""
    policy_fields: bool = False
    """hosts-b: `environment` and `host` in the exec-policy request."""
    exec_audit: bool = False
    """hosts-b: `environment.exec` rows carry tool, call_id, run_id, host_id, grant_id (and
    the redacted command for hosts with `audit_commands: redacted`)."""
    usage_tag: bool = False
    """hosts-b: requests made while an environment is selected carry the usage tag `env=<name>`."""
    chat_grants: bool = False
    """hosts-b: let a chat `/approve <id>` answer `ssh`/`sftp` cards (off: web UI or a rule only)."""
    rotate_grace_h: float = Field(default=24, ge=0, le=24 * 30)
    """hosts-b: hours the previous key keeps working after `auoreo ssh rotate`."""
    jump: bool = False
    """hosts-c: one-hop jump hosts (`jump: <host>` in a host file). Off: such a host is refused."""
    transfer: bool = False
    """hosts-c: the `env_transfer` tool (one file over the granted SSH connection)."""
    transfer_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=4 * 1024**3)
    """hosts-c: largest file `env_transfer` moves."""
    web: bool = False
    """hosts-c: `/api/hosts` and the Hosts page (fingerprints and state; no key operations)."""


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
    a job, webhook or connector session cannot call `kanban_create` directly. Required by the
    flags below."""
    kanban_suggestions: bool = False
    """The `kanban_create` suggestion action (requires `strict_origins`)."""
    triage: M7Triage = Field(default_factory=M7Triage)
    post_consent: bool = False
    """Connectors that consume `consent:post` (Mastodon) post only for an accepted suggestion
    (requires `strict_origins`)."""
    post_daily_cap: int = Field(default=10, ge=1, le=1000)
    """Consent-gated posts per plugin per UTC day."""
    voice_inbound: bool = False
    """The `voice.transcribe` host request: connector voice notes become `[voice] ` text
    (requires `strict_origins`)."""
    voice_max_seconds: int = Field(default=120, ge=1, le=3600)
    """Longest voice note transcribed (seconds, measured after decoding)."""
    voice_daily_seconds: int = Field(default=600, ge=1, le=86400)
    """Transcribed seconds per plugin per UTC day."""
    voice_max_bytes: int = Field(default=5 * 2**20, ge=1024, le=20 * 2**20)
    """Largest voice file accepted by `voice.transcribe` (bytes)."""

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

    # --- Wave 2 (docs/designs/m7-orchestration.md, "Wave 2 implementation notes")
    merge: bool = False
    """The `git_merge` tool: merge one human-approved commit of an `auoreo/*` branch into the
    server checkout (requires `worktrees`)."""
    merge_definitions: Literal["refuse", "warn"] = "refuse"
    """A merge that changes `.auoreo/`, `.github/workflows/`, `.gitattributes` or `.gitmodules`:
    refused (merge it by hand), or shown as a warning on the approval card."""
    merge_max_objects: int = Field(default=20000, ge=1, le=1_000_000)
    """Objects new to the target that `git_merge` re-hashes before the card; above it, refused."""
    conflict_board: str | None = None
    """Board of the kanban task a conflicting merge opens; default the source task's, else `default`."""
    reconcile_interval_s: float = Field(default=300.0, ge=0)
    """How often the server turns `<home>/worktrees/gc-journal.jsonl` into `worktree.removed`
    and `worktree.gc` events (0 = never)."""

    @model_validator(mode="after")
    def _worktrees_need_a_session_cwd(self) -> M7Orch:
        if self.worktrees and not self.session_cwd:
            raise ValueError("m7.orch.worktrees requires m7.orch.session_cwd (else isolation is prompt-only)")
        if self.merge and not self.worktrees:
            raise ValueError("m7.orch.merge requires m7.orch.worktrees")
        return self


class M7Settings(_Tolerant):
    token: M7Token = Field(default_factory=M7Token)
    ssh: M7Ssh = Field(default_factory=M7Ssh)
    agents: M7Agents = Field(default_factory=M7Agents)
    orch: M7Orch = Field(default_factory=M7Orch)
