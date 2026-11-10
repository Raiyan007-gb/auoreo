"""M7 W4: a git worktree per delegated task (`m7.orch.worktrees`, design `m7-orchestration.md`).

The shared half of `delegate_task(worktree=true)` and the kanban dispatcher: create or resume
the task's worktree under the orchestrator root, build the `worktree.created` payload and the
note the child reads first, undo a worktree whose spawn failed, and list the worktrees that
`auoreo worktree gc` must keep (those of claimed kanban tasks).

Nothing here merges: the branch holds the result, the user merges it (by hand or a PR) and
`worktree gc` removes the folder afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import KanbanCommentRow, KanbanTaskRow
from auoreo.runtime import worktrees as wt
from auoreo.runtime.cwd import orch_root
from auoreo.runtime.subtask import WorktreeError
from auoreo.schema.events import WorktreeCreated
from auoreo.teams.kanban import ATTACH_AUTHOR

if TYPE_CHECKING:
    from auoreo.core.db import Database
    from auoreo.core.services import Services

DISABLED = "worktree is disabled (m7.orch.worktrees)"
BRANCH_NOTE = "branch="
CHILD_NOTE = (
    "\n\nYou work in your own git worktree at `{path}` on branch `{branch}` (created from {base} of "
    "the parent checkout; its uncommitted changes were not carried over). Relative paths and commands "
    "run there. Commit your work on the current branch before finishing; do not merge or push."
)


async def prepare(services: Services, slug: str) -> wt.NewFeature:
    """Create (or resume) the worktree for `slug`. Raises `WorktreeError`; never falls back to
    the parent checkout."""
    orch = services.settings.m7.orch
    if not (orch.worktrees and orch.session_cwd):
        raise WorktreeError(DISABLED)
    root = orch_root(services.settings, services.cwd)
    return await wt.task_worktree(services.cwd, root, slug, max_worktrees=orch.max_worktrees)


async def rollback(services: Services, trees: list[wt.NewFeature]) -> None:
    for tree in trees:
        await wt.discard_worktree(services.cwd, tree)


def payload(tree: wt.NewFeature, *, task_id: int | None = None, board: str | None = None) -> dict[str, Any]:
    """`data` of the `worktree.created` event (emitted on the child session)."""
    event = WorktreeCreated(
        path=str(tree.path),
        branch=tree.branch,
        base=tree.base,
        resumed=tree.resumed,
        task_id=task_id,
        board=board,
    )
    return event.model_dump(exclude_none=True)


def child_note(tree: wt.NewFeature) -> str:
    return CHILD_NOTE.format(path=tree.path, branch=tree.branch, base=tree.base or "HEAD")


def summary(tree: wt.NewFeature) -> str:
    """One line for the parent's tool result (with the dirty-parent warning when there is one)."""
    verb = "resumed" if tree.resumed else "created"
    line = (
        f"worktree {verb}: {tree.path} on branch {tree.branch}; merge it yourself when the work is reviewed"
    )
    return "\n".join([line, *(f"warning: {n}" for n in tree.notes if "uncommitted" in n)])


async def protected_paths(db: Database) -> set[Path]:
    """Worktrees attached to claimed kanban tasks (`ref` = path, `note` = `branch=…`)."""
    q = (
        select(KanbanCommentRow.text)
        .join(KanbanTaskRow, KanbanTaskRow.id == KanbanCommentRow.task_id)
        .where(KanbanTaskRow.status == "claimed", KanbanCommentRow.author == ATTACH_AUTHOR)
    )
    async with db.session() as s:
        texts = list((await s.execute(q)).scalars())
    out: set[Path] = set()
    for text in texts:
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict) and str(record.get("note", "")).startswith(BRANCH_NOTE):
            out.add(Path(str(record.get("ref", ""))))
    return out
