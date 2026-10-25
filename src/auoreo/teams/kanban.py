"""Kanban-lite (spec §16.6): boards of tasks with an atomic claim, heartbeats, stale
reclaim and auto-block after repeated failures.

Claim settles at most once: one `UPDATE … WHERE id=(first ready) AND status='ready'`
statement and `rowcount == 1` decides the winner (SQLite serializes writers).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from auoreo.core.db import KanbanCommentRow, KanbanTaskRow, utcnow

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

FAILURE_LIMIT = 2
STALE_AFTER_S = 300
RECLAIM_EVERY_S = 60
STATUSES = ("ready", "claimed", "blocked", "done")


class KanbanError(ValueError):
    """A model-visible kanban failure (unknown task, wrong state)."""


def task_dict(row: KanbanTaskRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "board": row.board,
        "title": row.title,
        "body": row.body,
        "status": row.status,
        "worker": row.worker,
        "session_id": row.session_id,
        "failures": row.failures,
        "heartbeat_at": row.heartbeat_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class KanbanStore:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._reclaimer: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._reclaimer = asyncio.create_task(self._reclaim_loop(), name="kanban:reclaim")

    async def close(self) -> None:
        if self._reclaimer is not None:
            self._reclaimer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reclaimer
            self._reclaimer = None

    async def _reclaim_loop(self) -> None:
        while True:
            await asyncio.sleep(RECLAIM_EVERY_S)
            try:
                await self.reclaim_stale()
            except Exception:  # the dispatcher must keep ticking
                log.exception("kanban stale reclaim failed")

    async def _event(self, kind: str, row: KanbanTaskRow) -> None:
        await self.s.emit(
            "server", f"kanban.{kind}", {"id": row.id, "board": row.board, "worker": row.worker}
        )

    # ------------------------------------------------------------ operations
    async def create(self, board: str, title: str, body: str = "") -> KanbanTaskRow:
        row = KanbanTaskRow(board=board, title=title, body=body)
        async with self.s.db.session() as s:
            s.add(row)
            await s.commit()
        return row

    async def get(self, task_id: int) -> KanbanTaskRow:
        async with self.s.db.session() as s:
            row = await s.get(KanbanTaskRow, task_id)
        if row is None:
            raise KanbanError(f"unknown kanban task {task_id}")
        return row

    async def claim(self, board: str, worker: str, session_id: str | None = None) -> KanbanTaskRow | None:
        """Claim the oldest `ready` task on `board`; None when nothing is ready (or a
        concurrent claimer won the same row)."""
        first_ready = (
            select(KanbanTaskRow.id)
            .where(KanbanTaskRow.board == board, KanbanTaskRow.status == "ready")
            .order_by(KanbanTaskRow.id)
            .limit(1)
            .scalar_subquery()
        )
        now = utcnow()
        stmt = (
            update(KanbanTaskRow)
            .where(KanbanTaskRow.id == first_ready, KanbanTaskRow.status == "ready")
            .values(status="claimed", worker=worker, session_id=session_id, heartbeat_at=now, updated_at=now)
            .returning(KanbanTaskRow.id)
            .execution_options(synchronize_session=False)
        )
        async with self.s.db.session() as s:
            res = await s.execute(stmt)
            ids = list(res.scalars())
            await s.commit()
        if len(ids) != 1:
            return None
        row = await self.get(ids[0])
        await self._event("task_claimed", row)
        return row

    async def _transition(
        self, task_id: int, by_worker: str | None, /, *, allowed: tuple[str, ...], **values: Any
    ) -> KanbanTaskRow:
        async with self.s.db.session() as s:
            row = await s.get(KanbanTaskRow, task_id)
            if row is None:
                raise KanbanError(f"unknown kanban task {task_id}")
            if row.status not in allowed:
                raise KanbanError(f"task {task_id} is {row.status}; expected {' or '.join(allowed)}")
            if by_worker is not None and row.worker not in (None, by_worker):
                raise KanbanError(f"task {task_id} is claimed by {row.worker}, not {by_worker}")
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            await s.commit()
            return row

    async def heartbeat(self, task_id: int, worker: str | None = None) -> KanbanTaskRow:
        return await self._transition(task_id, worker, allowed=("claimed",), heartbeat_at=utcnow())

    async def complete(self, task_id: int, worker: str | None = None, summary: str = "") -> KanbanTaskRow:
        row = await self._transition(task_id, worker, allowed=("claimed", "ready"), status="done")
        if summary:
            await self.comment(task_id, worker or row.worker or "worker", summary)
        await self._event("task_done", row)
        return row

    async def block(self, task_id: int, reason: str, worker: str | None = None) -> KanbanTaskRow:
        """Record a failure. Below the limit the task goes back to `ready` for another
        attempt; at `FAILURE_LIMIT` it is blocked until a human/orchestrator steps in."""
        current = await self.get(task_id)
        failures = current.failures + 1
        status = "blocked" if failures >= FAILURE_LIMIT else "ready"
        row = await self._transition(
            task_id,
            worker,
            allowed=("claimed", "ready"),
            failures=failures,
            status=status,
            **({} if status == "blocked" else {"worker": None, "session_id": None, "heartbeat_at": None}),
        )
        await self.comment(task_id, worker or current.worker or "worker", f"blocked: {reason}")
        if status == "blocked":
            await self._event("task_blocked", row)
        return row

    async def comment(self, task_id: int, author: str, text: str) -> KanbanCommentRow:
        await self.get(task_id)
        row = KanbanCommentRow(task_id=task_id, author=author, text=text)
        async with self.s.db.session() as s:
            s.add(row)
            await s.commit()
        return row

    async def comments(self, task_id: int) -> list[KanbanCommentRow]:
        async with self.s.db.session() as s:
            q = (
                select(KanbanCommentRow)
                .where(KanbanCommentRow.task_id == task_id)
                .order_by(KanbanCommentRow.id)
            )
            return list((await s.execute(q)).scalars())

    async def list_tasks(
        self, board: str | None = None, status: str | None = None, limit: int = 500
    ) -> list[KanbanTaskRow]:
        async with self.s.db.session() as s:
            q = select(KanbanTaskRow).order_by(KanbanTaskRow.id).limit(limit)
            if board:
                q = q.where(KanbanTaskRow.board == board)
            if status:
                q = q.where(KanbanTaskRow.status == status)
            return list((await s.execute(q)).scalars())

    async def reclaim_stale(self, older_than_s: int = STALE_AFTER_S) -> list[int]:
        """Claimed tasks whose heartbeat is older than `older_than_s` go back to `ready`
        (counting as a failure, so a task that keeps stalling ends up blocked)."""
        cutoff = utcnow() - timedelta(seconds=older_than_s)
        async with self.s.db.session() as s:
            q = select(KanbanTaskRow.id).where(
                KanbanTaskRow.status == "claimed", KanbanTaskRow.heartbeat_at < cutoff
            )
            stale = list((await s.execute(q)).scalars())
        reclaimed: list[int] = []
        for task_id in stale:
            try:
                await self.block(task_id, f"no heartbeat for {older_than_s}s; reclaimed")
            except KanbanError:  # completed meanwhile
                continue
            reclaimed.append(task_id)
        return reclaimed
