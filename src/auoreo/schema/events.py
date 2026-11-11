"""The single event envelope used by SSE (and later WS). Pydantic models are the wire contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Event types that may be dropped/coalesced under backpressure (spec §5.3).
COALESCABLE = frozenset({"message.delta", "tool.progress", "ping"})


class ServerEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(description="str(seq) — use as SSE id / Last-Event-ID")
    seq: int = Field(description="Global monotonic sequence (DB autoincrement)")
    type: str = Field(description="e.g. run.started, message.delta, tool.completed")
    aggregate_id: str = Field(description="Session id, or 'server'")
    aggregate_seq: int = Field(description="Monotonic sequence within the aggregate")
    data: dict[str, Any]
    created_at: datetime


class ToolProgress(BaseModel):
    """`data` of a `tool.progress` event (spec §25.7 progress contract): at most one per call
    every `tool_engine.progress_interval_ms` (a final `percent: 100` is always sent)."""

    run_id: str | None = None
    call_id: str
    tool: str
    phase: Literal["starting", "running", "waiting", "finishing"] = "running"
    message: str = Field(default="", max_length=500)
    percent: float | None = Field(default=None, ge=0, le=100)
    cancellable: bool = False
    data: dict[str, Any] | None = None
    """Tool-specific payload, validated against the tool's `progressSchema` (I6)."""


class ToolPartial(BaseModel):
    """`data` of a `tool.partial` event (spec §25.6): one streamed fragment of a tool call's
    arguments. Render-only — nothing executes until the call is committed (`tool.started`)."""

    run_id: str | None = None
    call_id: str
    name: str = ""
    arg_delta: str
    seq: int = Field(ge=0)


class ToolPartialDrop(BaseModel):
    """`data` of a `tool.partial_drop` event: partial calls abandoned (stream failed, fallback,
    malformed call) — clients tear their drafts down."""

    run_id: str | None = None
    call_ids: list[str]


TASK_REF = "`kanban:<board>#<id>`, `delegate:<parent_session_id>/<name>` (`workflow:<run>/<step>` reserved)"


class WorktreeCreated(BaseModel):
    """`data` of a `worktree.created` event (M7 W4), emitted on the child session that runs in
    the worktree. `resumed` = an existing folder or branch was reused (never reset)."""

    path: str
    branch: str
    base: str = Field(description="Short sha of the server checkout's HEAD the branch started from")
    resumed: bool = False
    task_id: int | None = Field(default=None, description="Kanban task, when the dispatcher made it")
    board: str | None = None
    task_ref: str | None = Field(default=None, description=TASK_REF)


# --- M7 W4 Wave 2: merge-back and worktree reconcile (all optional `task_ref`, no nesting)
class ReviewRequested(BaseModel):
    """`data` of `review.requested`, emitted on the worker session when it ends (the harness
    records the branch tip itself) or by `git_merge` when no record exists for the commit."""

    branch: str
    sha: str
    base: str = ""
    commits: int = 0
    files: int = 0
    task_ref: str | None = Field(default=None, description=TASK_REF)


class ReviewDecision(BaseModel):
    """`data` of `review.approved` / `review.rejected` (a record, never a grant)."""

    branch: str
    sha: str
    target: str
    target_sha: str
    request_id: str | None = None
    task_ref: str | None = Field(default=None, description=TASK_REF)


class MergeDenied(BaseModel):
    """`data` of `merge.denied`: `git_merge` refused before merging; nothing changed."""

    branch: str
    sha: str
    reason: str = Field(max_length=64)
    task_ref: str | None = Field(default=None, description=TASK_REF)


class MergeConflicts(BaseModel):
    """`data` of `merge.conflicts`: the dry run conflicted; the checkout is untouched and a
    kanban task (`conflict_task`) tracks the resolution."""

    branch: str
    sha: str
    target: str
    target_sha: str
    files: list[str] = Field(default_factory=list, max_length=50)
    conflict_task: int | None = None
    task_ref: str | None = Field(default=None, description=TASK_REF)


class MergeCompleted(BaseModel):
    branch: str
    sha: str
    target: str
    target_sha: str
    merge_sha: str
    task_ref: str | None = Field(default=None, description=TASK_REF)


class MergeFailed(BaseModel):
    """`data` of `merge.failed`: git failed after the dry run passed; the merge was aborted."""

    branch: str
    sha: str
    message: str = Field(max_length=500)
    task_ref: str | None = Field(default=None, description=TASK_REF)


class WorktreeRemoved(BaseModel):
    """`data` of `worktree.removed` (aggregate `server`), from the gc journal."""

    path: str
    branch: str | None = None
    reason: Literal["merged", "stale", "missing", "orphan"]
    task_ref: str | None = Field(default=None, description=TASK_REF)


class WorktreeGc(BaseModel):
    """`data` of `worktree.gc` (aggregate `server`): one summary per drained journal."""

    removed: int = 0
    pruned: int = 0
    branches_deleted: int = 0
    kept: int = 0
