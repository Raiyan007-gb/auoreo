"""Request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from auoreo.schema.voice_api import VoiceBackendStatus as VoiceBackendStatus  # re-export
from auoreo.schema.voice_api import VoiceSegment as VoiceSegment  # re-export
from auoreo.schema.voice_api import VoiceSpeakRequest as VoiceSpeakRequest  # re-export
from auoreo.schema.voice_api import VoiceStatus as VoiceStatus  # re-export
from auoreo.schema.voice_api import VoiceTranscription as VoiceTranscription  # re-export


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(_In):
    message: str = Field(min_length=1, max_length=1_000_000)
    session_id: str | None = None
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None
    mode: Literal["followup", "steer", "inject"] = "followup"
    request_id: str | None = Field(default=None, description="Idempotency key (UUID)")


class ChatAccepted(BaseModel):
    session_id: str
    run_id: str | None
    deduped: bool = False


class SessionCreate(_In):
    title: str | None = None
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None


class SessionInfo(BaseModel):
    id: str
    title: str
    agent: str
    workflow: str
    model: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    project: str | None = Field(
        default=None, description="Working directory the session was created in (I5; list only)."
    )


class MessageOut(BaseModel):
    idx: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class PermissionReply(_In):
    reply: Literal["once", "always", "reject"]
    message: str | None = Field(default=None, max_length=4000)
    cascade: bool = Field(
        default=False,
        description="With `once`: also approve every pending request in the same approval group "
        "(permission, cwd, risk tier, tool) across sessions.",
    )
    updated_input: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("updated_input", "updatedInput"),
        description="Allow with edited tool arguments (spec §5.2 `allow{updatedInput}`); only for "
        "requests marked `editable`. Re-validated against the tool schema, approved once, audited.",
    )


class PendingPermission(BaseModel):
    request_id: str
    session_id: str
    tool: str
    permission: str
    pattern: str
    detail: str
    one_shot: bool = Field(default=False, description="No `always`: single-use (e.g. sandbox escalation).")
    risk: str = "medium"
    editable: bool = Field(default=False, description="The reply may carry `updated_input`.")
    input: dict[str, Any] | None = Field(default=None, description="Arguments for the edit form.")
    justification: str | None = Field(default=None, description="Why the agent asks (escalation).")


class HarnessEntry(BaseModel):
    kind: str
    name: str
    source: str
    path: str | None
    description: str
    available: bool | None = None
    """Tools only: false when hidden by `is_enabled`, `env_gate` or `check_fn`."""
    unavailable_reason: str | None = None
    emoji: str | None = None
    activity_hint: str | None = None
    """Tools only: short UI label template shown while the tool runs (I6)."""


class HarnessListing(BaseModel):
    generation: int
    entries: dict[str, list[HarnessEntry]]
    errors: list[str]


class ReloadResult(BaseModel):
    generation: int
    errors: list[str]
    changes: dict[str, dict[str, list[str]]]
    kind: str | None = Field(default=None, description="The `?kind=` scope (I5); None = everything.")
    note: str | None = None


class BundledMcpResult(BaseModel):
    """`POST /api/harness/mcp/bundled/{name}`: the runtime definition written, then reloaded."""

    name: str
    path: str = Field(description="The `<home>/mcp/<name>.yaml` that now enables the server.")
    generation: int
    errors: list[str]


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    generation: int
    gateway: str
    code_sha: str | None = None
    """Fingerprint of the running code (`auoreo doctor` compares it with the local install)."""


class UsageRecord(BaseModel):
    id: int
    session_id: str
    run_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float | None
    estimated: bool
    created_at: datetime
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    generation_id: str | None = None
    deployment: str | None = None


class UsageTotals(BaseModel):
    key: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class UsageSummary(BaseModel):
    days: int
    total: UsageTotals
    by_model: list[UsageTotals]
    by_session: list[UsageTotals]


class CompactRequest(_In):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    retain_ratio: float | None = Field(default=None, ge=0, le=1)
    up_to: int | None = Field(default=None, ge=0)
    """Partial compaction: replace messages before this history index, keep the rest."""
    from_: int | None = Field(default=None, ge=0, alias="from")
    """Partial compaction: replace messages from this history index to the end."""


class CompactionOut(BaseModel):
    compaction_id: str
    source: str = "summary"
    """`summary` (LLM), `session_memory` (notes file, no LLM) or `prune` (S2 only)."""
    pre_tokens: int
    post_tokens: int
    archived: int
    pruned: int
    summary_chars: int


class ArchivedMessage(BaseModel):
    compaction_id: str
    idx: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    archived_at: datetime


class LatestSeq(BaseModel):
    seq: int


class SessionDetail(SessionInfo):
    """`SessionInfo` plus lineage/archive state (fork, archive endpoints)."""

    parent_id: str | None = None
    archived: bool = False


class SessionDeleted(BaseModel):
    id: str
    deleted: bool


class CommandInfo(BaseModel):
    name: str
    description: str
    handler: Literal["prompt", "compact", "model", "plan"]
    args_hint: str


# --- M4e WebRTC signaling
class RtcOffer(_In):
    """Client SDP offer. The client must create the `auoreo-events` data channel first."""

    sdp: str = Field(description="Offer SDP (≤ 64 KiB, else 413); never logged")
    type: Literal["offer"] = "offer"
    session_id: str | None = Field(default=None, description="Auto-subscribe the events channel to it")


class RtcAnswer(BaseModel):
    rtc_id: str
    sdp: str
    type: Literal["answer"] = "answer"


class RtcPeerInfo(BaseModel):
    rtc_id: str
    session_id: str | None
    state: str = Field(description="RTCPeerConnection.connectionState")
    created_at: datetime


class RtcClosed(BaseModel):
    closed: bool


# --- M5a automation: scheduler, webhooks, deliveries
class CronRunInfo(BaseModel):
    id: int
    job: str
    scheduled_for: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status: str = Field(description="pending | running | ok | failed | skipped")
    session_id: str | None
    catch_up: bool
    error: str | None


class CronJobInfo(BaseModel):
    name: str
    description: str
    schedule: str
    tz: str | None
    agent: str
    deliver: list[str]
    enabled: bool
    paused: bool
    error: str | None = Field(default=None, description="Schedule parse error, if any")
    next_run_at: datetime | None
    last_run: CronRunInfo | None


class CronRunAccepted(BaseModel):
    job: str
    run_id: int
    status: str


class CronPaused(BaseModel):
    job: str
    paused: bool


class WebhookRouteInfo(BaseModel):
    name: str
    path: str
    description: str
    provider: str = Field(description="github | generic | gitlab | svix | telegram-callback | <plugin kind>")
    events: list[str]
    filters: list[dict[str, Any]]
    agent: str
    workflow: str | None
    deliver: list[str]
    enabled: bool
    rate_limit_per_minute: int
    max_body_bytes: int
    secret_configured: bool = Field(description="Whether the referenced secret resolves (value never shown)")


class HookAck(BaseModel):
    status: Literal["accepted", "ignored", "duplicate", "delivered", "failed"]
    route: str | None = None
    event: str | None = None
    delivery_id: int | None = None


class HooksHealth(BaseModel):
    routes: list[str]


class DeliveryInfo(BaseModel):
    id: int
    kind: str
    source: str
    external_id: str
    status: str = Field(description="pending | delivering | delivered | failed | unknown")
    session_id: str | None
    target: str
    attempts: int
    error: str | None
    result: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None


class DeliveryReplay(BaseModel):
    dry_run: bool
    prompt: str
    delivery: DeliveryInfo | None = Field(default=None, description="The new pending row (not on dry run)")


# --- M5d teams + kanban
class TeamInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    lead_session_id: str
    created_at: datetime
    members: int = 0


class TeamMemberInfo(BaseModel):
    name: str = Field(description='Member name; "lead" is the team lead session')
    agent: str
    session_id: str
    depth: int
    status: str = Field(description="lead | queued | running | done | failed | stopped")
    running: bool = Field(description="Liveness: a run is in progress right now")


class TeamDetail(TeamInfo):
    peers: list[TeamMemberInfo] = Field(default_factory=list)


class MailMessage(BaseModel):
    id: int
    team_id: str
    recipient: str
    sender: str
    type: str
    summary: str
    text: str
    read: bool
    created_at: datetime


class KanbanTask(BaseModel):
    id: int
    board: str
    title: str
    body: str
    status: Literal["ready", "claimed", "blocked", "done"]
    worker: str | None = None
    session_id: str | None = None
    failures: int = 0
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KanbanCreate(_In):
    board: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)


# --- M5e plugins
PluginStatus = Literal[
    "running", "stopped", "crashed", "disabled", "invalid_config", "unverified", "quarantined"
]


class PluginInfo(BaseModel):
    name: str
    version: str
    description: str
    status: PluginStatus
    provides: list[str]
    tools: list[str] = Field(description="Registered dynamic tools (`plugin__<name>__<tool>`)")
    deliveries: list[str] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)
    error: str | None = None
    verify: Literal["ok", "mismatch", "missing"] = Field(description="lock.json hash vs the plugin folder")
    tier: str | None = Field(default=None, description="bundled | allow-listed | local-path (I6)")
    signed_by: str | None = Field(default=None, description="ed25519 key id that signed it (I6)")


class PluginSkillInfo(BaseModel):
    name: str
    description: str
    body: str


class PluginPair(_In):
    code: str = Field(pattern=r"^\d{6}$", description="6-digit pairing code shown to the remote chat")


class PluginPaired(BaseModel):
    paired: bool
    chat_id: str | None = None


# --- M5f memory lifecycle + curator
class CuratorMerge(BaseModel):
    fact_id: int
    into: int


class CuratorContradiction(BaseModel):
    new_id: int
    existing_id: int


class CuratorReport(BaseModel):
    """One deterministic curator pass (no LLM). Lists hold fact ids."""

    dry_run: bool
    skipped: str | None = Field(default=None, description="Why the pass did not run (a guard fired)")
    scanned: int = 0
    archived: list[int] = Field(
        default_factory=list, description="Archived as unused past archive_after_days"
    )
    stale: list[int] = Field(default_factory=list, description="Unused past stale_after_days (reported only)")
    skipped_pinned: int = 0
    duplicates_merged: list[CuratorMerge] = Field(default_factory=list)
    contradictions: list[CuratorContradiction] = Field(
        default_factory=list, description="Pending contradiction flags (both facts still active)"
    )
    ledger_path: str
    report_path: str | None = None


class CuratorReportText(BaseModel):
    path: str
    markdown: str


class FactPin(_In):
    pinned: bool = True


class FactFeedback(_In):
    helpful: bool


class FactActionResult(BaseModel):
    id: int
    ok: bool
    trust: float | None = None
