"""H4: operator moves on kanban tasks (spec §16.6 "orchestrator adds list/unblock").

`KanbanStore` models the *worker* lifecycle (claim → heartbeat → complete / failure-counted
block). A human or orchestrator also needs to put a task anywhere directly: unblock it,
park it, block it right away with a reason, or move it to another board. That lives here so
`auoreo kanban move|block` stay thin wrappers over a service.
"""

from __future__ import annotations

from typing import Any

from auoreo.core.db import KanbanTaskRow, utcnow
from auoreo.teams.kanban import STATUSES, KanbanError, KanbanStore

MOVABLE = ("ready", "blocked", "done")
"""`claimed` is only reachable through `claim` (it needs a worker)."""


async def move(
    store: KanbanStore,
    task_id: int,
    *,
    status: str | None = None,
    board: str | None = None,
    by: str = "operator",
    reason: str = "",
) -> KanbanTaskRow:
    """Set a task's status and/or board. Back to `ready` clears the worker and the failure
    count (an explicit unblock is a fresh start); every move leaves a comment."""
    if status is None and board is None:
        raise KanbanError("nothing to move: give a status and/or a board")
    if status is not None and status not in MOVABLE:
        raise KanbanError(f"cannot move to '{status}' (one of {', '.join(MOVABLE)}; of {STATUSES})")
    async with store.s.db.session() as s:
        row = await s.get(KanbanTaskRow, task_id)
        if row is None:
            raise KanbanError(f"unknown kanban task {task_id}")
        before = (row.status, row.board)
        changes: dict[str, Any] = {}
        if board is not None:
            changes["board"] = board
        if status is not None:
            changes["status"] = status
            if status == "ready":
                changes.update(worker=None, session_id=None, heartbeat_at=None, failures=0)
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = utcnow()
        await s.commit()
    note = f"moved {before[0]}@{before[1]} → {row.status}@{row.board}" + (f": {reason}" if reason else "")
    await store.comment(task_id, by, note)
    await store.s.emit(
        "server", "kanban.task_moved", {"id": row.id, "board": row.board, "status": row.status}
    )
    return row
