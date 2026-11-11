"""M7 W4 Wave 2: `auoreo worktree gc --reconcile` and the `worktree.removed|gc` events.

The CLI never writes an event: `EventStore.append` sequences under an in-process lock, so an
append from a second process next to a running server can collide and would not reach live
subscribers. Instead:

- `plan` extends `worktrees.plan_gc` with what git, the worktree roots and the task store say
  about `auoreo/*` branches: a merged branch without a worktree is deleted (`git branch -d`,
  never `-D`), an unmerged one is listed and kept, a kanban attachment whose worktree folder is
  gone or whose branch is gone is reported (with the tip the harness last recorded, which
  `--restore` recreates while the commit exists);
- `apply` removes, prunes and deletes, and appends one JSON line per action to
  `<home>/worktrees/gc-journal.jsonl` (paths and names only, rotated at 1 MiB);
- the server pass (`start`, one `STARTERS` line) drains that journal every
  `m7.orch.reconcile_interval_s` into one `worktree.removed` per removal and one `worktree.gc`
  summary on aggregate `server`. The server never removes a worktree itself.

Live sessions are protected through the task store (`teams.worktree_gc.guard`: claimed tasks,
running members and sessions). Everything here needs `m7.orch.worktrees`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from auoreo.runtime import worktree_git as wg
from auoreo.runtime import worktrees as wt
from auoreo.runtime.subtask import WorktreeError
from auoreo.schema.events import WorktreeGc, WorktreeRemoved

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
JOURNAL = "gc-journal.jsonl"
DRAINING = "gc-journal.draining.jsonl"
ROTATED = "gc-journal.1.jsonl"
ROTATE_BYTES = 1 << 20
REASONS = {"merged": "merged", "untouched": "stale", "folder missing": "missing", "orphan": "orphan"}


def _real(path: Path | str) -> Path:
    return Path(path).resolve()


def _exists(path: Path) -> bool:
    return path.exists()


def journal_dir(home: Path) -> Path:
    return Path(home) / "worktrees"


@dataclass
class Finding:
    kind: str  # merged_branch | unmerged_branch | missing_worktree | missing_branch
    branch: str | None
    action: str  # delete_branch | keep | report | restore
    reason: str
    path: str | None = None
    tip: str | None = None
    task_ref: str | None = None


@dataclass
class Records:
    """What the task store and the harness's own events say (read-only)."""

    attachments: list[tuple[str, str, str]] = field(default_factory=list)  # (task_ref, path, branch)
    tips: dict[str, str] = field(default_factory=dict)  # branch -> last recorded tip
    refs: dict[str, str] = field(default_factory=dict)  # worktree path -> task_ref


async def records(db: Any) -> Records:
    from sqlalchemy import select

    from auoreo.core.db import EventRow, KanbanCommentRow, KanbanTaskRow
    from auoreo.teams.kanban import ATTACH_AUTHOR
    from auoreo.teams.merge_records import task_ref_of

    out = Records()
    async with db.session() as s:
        q = (
            select(KanbanTaskRow.board, KanbanTaskRow.id, KanbanCommentRow.text)
            .join(KanbanTaskRow, KanbanTaskRow.id == KanbanCommentRow.task_id)
            .where(KanbanCommentRow.author == ATTACH_AUTHOR, KanbanTaskRow.status != "done")
        )
        for board, task_id, text in (await s.execute(q)).all():
            with contextlib.suppress(ValueError, AttributeError):
                rec = json.loads(text)
                note = str(rec.get("note", ""))
                if note.startswith("branch="):
                    branch = note.removeprefix("branch=").split(" ", 1)[0]
                    out.attachments.append((f"kanban:{board}#{task_id}", str(rec.get("ref", "")), branch))
        evq = (
            select(EventRow)
            .where(EventRow.type.in_(("worktree.created", "review.requested")))
            .order_by(EventRow.seq)
        )
        for row in (await s.execute(evq)).scalars():
            data = db.cipher.decrypt(row.data)
            if row.type == "review.requested" and data.get("branch") and data.get("sha"):
                out.tips[str(data["branch"])] = str(data["sha"])
            elif row.type == "worktree.created" and data.get("path") and (ref := task_ref_of(data)):
                out.refs.setdefault(str(_real(str(data["path"]))), ref)
    return out


async def records_from_settings(st: Any, warn: Any = None) -> Records:
    from auoreo.core.db import Database

    if not st.db_path.exists():
        return Records()
    try:
        db = Database(st.db_path, encryption=st.db_encryption)
        await db.init()
        try:
            return await records(db)
        finally:
            await db.close()
    except Exception as exc:
        if warn is not None:
            warn(f"task store not readable ({exc})")
        return Records()


# ------------------------------------------------------------------ plan
async def _branches(top: Path) -> dict[str, str]:
    out = await wg.repo_git(
        top, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/auoreo/"
    )
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


async def _worktree_branches(top: Path) -> dict[str, Path]:
    listing = await wt._ok("worktree", "list", "--porcelain", cwd=top) or ""
    return {
        e["branch"].removeprefix("refs/heads/"): _real(e["worktree"])
        for e in wt._entries(listing)
        if e.get("branch", "").startswith("refs/heads/")
    }


async def plan(
    top: Path, items: list[wt.GcItem], rec: Records, protected: set[Path], *, restore: bool = False
) -> list[Finding]:
    """Branch and attachment findings beside the gc items (`items` already planned)."""
    for path in {p for p in (await _worktree_branches(top)).values()}:
        await wg.import_objects(top, path)  # branch reads below need the workers' commits
    branches = await _branches(top)
    on = await _worktree_branches(top)
    going = {_real(i.path) for i in items if i.action in ("remove", "prune")}
    guarded = {_real(p) for p in protected}
    claimed = {b for _, ref, b in rec.attachments if _real(ref) in guarded}
    out: list[Finding] = []
    for branch, tip in sorted(branches.items()):
        tree = on.get(branch)
        if (tree is not None and tree not in going) or branch in claimed:
            continue
        merged = await wt._ok(*wg.HARDEN, "merge-base", "--is-ancestor", tip, "HEAD", cwd=top) is not None
        ref = rec.refs.get(str(tree)) if tree else None
        if merged:
            out.append(Finding("merged_branch", branch, "delete_branch", "merged into HEAD", task_ref=ref))
        else:
            out.append(
                Finding("unmerged_branch", branch, "keep", "not merged; no worktree", tip=tip, task_ref=ref)
            )
    for task_ref, ref, branch in rec.attachments:
        path = _real(ref)
        if not _exists(path) and path not in going:
            out.append(Finding("missing_worktree", branch, "report", "attached worktree folder is gone; "
                               "a re-dispatch recreates it", path=str(path), task_ref=task_ref))  # fmt: skip
        if branch.startswith(wt.BRANCH_PREFIX) and branch not in branches:
            last = rec.tips.get(branch)
            known = (
                last is not None and await wt._ok("cat-file", "-e", f"{last}^{{commit}}", cwd=top) is not None
            )
            action = "restore" if restore and known else "report"
            why = f"branch missing; last recorded tip {last}" if last else "branch missing; no recorded tip"
            out.append(
                Finding("missing_branch", branch, action, why, tip=last if known else None, task_ref=task_ref)
            )
    return out


# ------------------------------------------------------------------ apply + journal
def _reason(item: wt.GcItem) -> str:
    return next((v for k, v in REASONS.items() if item.reason.startswith(k)), "stale")


def write_journal(home: Path, lines: list[dict[str, Any]]) -> None:
    """Append `lines` in one write (rotating at `ROTATE_BYTES`); paths and names only."""
    if not lines:
        return
    folder = journal_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / JOURNAL
    with contextlib.suppress(OSError):
        if path.stat().st_size > ROTATE_BYTES:
            os.replace(path, folder / ROTATED)
    blob = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(blob)


async def apply(
    top: Path,
    items: list[wt.GcItem],
    findings: list[Finding],
    home: Path,
    *,
    refs: dict[str, str] | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Carry out the plan (worktrees first, then branches); journal every action that
    succeeded. `refs`: worktree path -> `task_ref`. Returns the journal lines."""
    on = {v: k for k, v in (await _worktree_branches(top)).items()}
    refs = refs or {}
    now: float = time.time()
    lines: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        if item.action == "keep":
            continue
        try:
            await wt.apply_gc(top, [item], registered_force=force)
        except (WorktreeError, OSError) as exc:
            errors.append(f"{item.path}: {exc}")
            continue
        branch = on.get(_real(item.path))
        lines.append({"ts": now, "path": str(item.path), "branch": branch, "action": item.action,
                      "reason": _reason(item), "task_ref": refs.get(str(_real(item.path)))})  # fmt: skip
    for f in findings:
        if f.action == "delete_branch" and f.branch:
            if await wt._ok(*wg.HARDEN, "branch", "-d", "--", f.branch, cwd=top) is None:
                errors.append(f"{f.branch}: git branch -d refused")
                continue
            lines.append({"ts": now, "branch": f.branch, "action": "delete_branch", "reason": "merged",
                          "task_ref": f.task_ref})  # fmt: skip
        elif f.action == "restore" and f.branch and f.tip:
            if await wt._ok(*wg.HARDEN, "branch", "--", f.branch, f.tip, cwd=top) is not None:
                lines.append({"ts": now, "branch": f.branch, "action": "restore", "reason": "missing"})
    kept = sum(1 for i in items if i.action == "keep") + sum(1 for f in findings if f.action == "keep")
    lines.append({"ts": now, "action": "summary", "kept": kept, "errors": len(errors)})
    write_journal(home, lines)
    if errors:
        raise WorktreeError("; ".join(errors)[:1000])
    return lines


# ------------------------------------------------------------------ server pass
def _take(folder: Path) -> list[dict[str, Any]]:
    draining = folder / DRAINING
    if not draining.exists():
        try:
            os.replace(folder / JOURNAL, draining)
        except OSError:  # no journal (or Windows: the CLI holds it open): next interval
            return []
    try:
        text = draining.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        with contextlib.suppress(ValueError):
            if isinstance(line := json.loads(raw), dict):
                out.append(line)
    with contextlib.suppress(OSError):
        draining.unlink()
    return out


async def drain(services: Services) -> int:
    """Turn journal lines into events; returns how many `worktree.removed` were emitted."""
    lines = _take(journal_dir(services.settings.home))
    if not lines:
        return 0
    count = {"remove": 0, "prune": 0, "delete_branch": 0}
    kept = emitted = 0
    for line in lines:
        action = line.get("action")
        if action == "summary":
            with contextlib.suppress(TypeError, ValueError):
                kept += max(0, int(line.get("kept") or 0))
            continue
        if action not in count:
            continue
        count[str(action)] += 1
        if action == "delete_branch":
            continue
        try:
            event = WorktreeRemoved(
                path=str(line.get("path", ""))[:2000],
                branch=(str(line["branch"])[:300] if line.get("branch") else None),
                reason=line.get("reason", "stale"),
                task_ref=(str(line["task_ref"])[:300] if line.get("task_ref") else None),
            )
        except ValidationError:
            continue
        await services.emit("server", "worktree.removed", event.model_dump(exclude_none=True))
        emitted += 1
    summary = WorktreeGc(
        removed=count["remove"], pruned=count["prune"], branches_deleted=count["delete_branch"], kept=kept
    )
    await services.emit("server", "worktree.gc", summary.model_dump())
    return emitted


class ReconcilePass:
    def __init__(self, services: Services, interval_s: float) -> None:
        self.s = services
        self.interval_s = interval_s
        self.task: asyncio.Task[None] | None = asyncio.create_task(self._loop(), name="worktree:reconcile")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await drain(self.s)
            except Exception:  # a report; keep the server running
                log.exception("draining the worktree gc journal failed")

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None


async def start(services: Services) -> ReconcilePass | None:
    orch = services.settings.m7.orch
    if not orch.worktrees or orch.reconcile_interval_s <= 0:
        return None
    return ReconcilePass(services, orch.reconcile_interval_s)


def finding_dict(f: Finding) -> dict[str, Any]:
    return {k: v for k, v in asdict(f).items() if v is not None}
