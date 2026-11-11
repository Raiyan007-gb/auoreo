"""M7 W4 Wave 2: what `git_merge` knows about a branch, read from server-written records only.

`worktree.created` and `review.requested` are append-only events that only the harness emits,
so a worker cannot fake them (kanban attachments and comments, by contrast, are writable by
any worker on the board). From them come the branch's base (D3), the tip the harness saw when
the worker ended (D2), the worker session and the `task_ref`.

Also here: the tip record itself (`record_tip`, called when a worktree worker ends) and the
conflict task (created `blocked`, deduplicated on (source sha, target HEAD)).
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import EventRow, KanbanCommentRow
from auoreo.runtime import merge as mg
from auoreo.runtime import worktree_git as wg
from auoreo.runtime.subtask import WorktreeError
from auoreo.schema.events import ReviewRequested

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
KANBAN_REF = re.compile(r"kanban:([A-Za-z0-9_.-]{1,64})#(\d+)")
MERGE_AUTHOR = "__merge__"
"""Author of the conflict-dedupe comment; a worker's comments carry its session id."""


@dataclass
class Record:
    """The first `worktree.created` of a branch."""

    branch: str
    path: Path
    base: str
    session_id: str
    task_ref: str | None = None

    @property
    def kanban(self) -> tuple[str, int] | None:
        m = KANBAN_REF.fullmatch(self.task_ref or "")
        return (m.group(1), int(m.group(2))) if m else None


async def _events(
    services: Services, kind: str, aggregate: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    q = select(EventRow).where(EventRow.type == kind).order_by(EventRow.seq)
    if aggregate is not None:
        q = q.where(EventRow.aggregate_id == aggregate)
    async with services.db.session() as s:
        rows = list((await s.execute(q)).scalars())
    return [(r.aggregate_id, services.db.cipher.decrypt(r.data)) for r in rows]


def task_ref_of(data: dict[str, Any]) -> str | None:
    if isinstance(ref := data.get("task_ref"), str) and ref:
        return ref
    if data.get("board") and isinstance(data.get("task_id"), int):
        return f"kanban:{data['board']}#{data['task_id']}"
    return None


def _record(aggregate: str, data: dict[str, Any]) -> Record:
    return Record(
        str(data.get("branch", "")),
        Path(str(data.get("path", ""))),
        str(data.get("base", "")),
        aggregate,
        task_ref_of(data),
    )


async def created_for(services: Services, branch: str) -> Record | None:
    for aggregate, data in await _events(services, "worktree.created"):
        if data.get("branch") == branch and data.get("base"):
            return _record(aggregate, data)
    return None


async def created_on(services: Services, session_id: str) -> Record | None:
    found = await _events(services, "worktree.created", session_id)
    return _record(*found[0]) if found else None


async def recorded_tips(services: Services, branch: str) -> list[str]:
    """Every tip `review.requested` recorded for `branch`, oldest first."""
    return [
        str(d.get("sha")) for _, d in await _events(services, "review.requested") if d.get("branch") == branch
    ]


async def review_payload(top: Path, rec: Record, sha: str) -> dict[str, Any]:
    base = rec.base if mg.SHA_RE.fullmatch(rec.base) or re.fullmatch(r"[0-9a-f]{4,64}", rec.base) else ""
    commits = files = 0
    if base:
        try:
            commits = len(await mg.commits(top, base, sha))
            files = len(await mg.changes(top, base, sha))
        except WorktreeError:
            pass
    event = ReviewRequested(
        branch=rec.branch, sha=sha, base=base, commits=commits, files=files, task_ref=rec.task_ref
    )
    return event.model_dump(exclude_none=True)


async def record_tip(services: Services, session_id: str) -> None:
    """When a worktree worker ends: record its branch tip (`review.requested` on the worker
    session). No-op without `m7.orch.merge` or for a session that has no worktree. Never raises."""
    if not services.settings.m7.orch.merge:
        return
    try:
        rec = await created_on(services, session_id)
        if rec is None or not rec.branch:
            return
        top = await mg.toplevel(services.cwd)
        await wg.import_objects(top, rec.path)
        sha = await mg.tip(top, rec.branch)
        if sha is None:
            return
        await services.emit(session_id, "review.requested", await review_payload(top, rec, sha))
    except Exception:  # a report, never a reason to fail the worker's finish
        log.exception("recording the branch tip of %s failed", session_id)


# ------------------------------------------------------------------ conflict task
def dedupe_key(sha: str, head: str) -> str:
    return f"merge-conflict={sha}:{head}"


async def find_conflict_task(services: Services, sha: str, head: str) -> int | None:
    q = select(KanbanCommentRow.task_id).where(
        KanbanCommentRow.author == MERGE_AUTHOR, KanbanCommentRow.text == dedupe_key(sha, head)
    )
    async with services.db.session() as s:
        found = (await s.execute(q.limit(1))).scalar()
    return int(found) if found is not None else None


async def conflict_task(
    services: Services, rec: Record, *, sha: str, head: str, target: str, files: list[str]
) -> int | None:
    """Find or create the kanban task that tracks resolving this conflict; None without teams."""
    teams = services.ext.get("teams")
    if teams is None:
        return None
    if (known := await find_conflict_task(services, sha, head)) is not None:
        return known
    kb = teams.kanban
    source = rec.kanban
    board = services.settings.m7.orch.conflict_board or (source[0] if source else "default")
    body = (
        f"Merging {rec.branch} at {sha} onto {target} at {head} conflicts in: {', '.join(files[:50])}.\n"
        f"Work in the branch's worktree, merge {target} into it and commit; then call git_merge again "
        "with the new tip (a new approval is needed)."
    )
    row = await kb.create_blocked(board, f"Resolve merge conflicts: {rec.branch} onto {target}"[:200], body)
    await kb.comment(row.id, MERGE_AUTHOR, dedupe_key(sha, head))
    await kb.attach(row.id, str(rec.path), note=f"branch={rec.branch}", by=MERGE_AUTHOR)
    if source is not None:
        with contextlib.suppress(ValueError):  # the source task was deleted
            await kb.link(row.id, "relates", source[1], by=MERGE_AUTHOR)
    return int(row.id)


async def conflict_branch(services: Services, task_id: int) -> tuple[str, str] | None:
    """`(branch, sha)` when `task_id` is a conflict task the harness opened (from its own
    `merge.conflicts` events, never from an attachment a worker could add)."""
    for _, data in reversed(await _events(services, "merge.conflicts")):
        if data.get("conflict_task") == task_id and data.get("branch") and data.get("sha"):
            return str(data["branch"]), str(data["sha"])
    return None
