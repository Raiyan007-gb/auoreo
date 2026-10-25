"""Long-running user goals (spec §2.3, §24.10): `get/create/update_goal` + `/api/goals`.

A goal tracks progress (0..1) and the sessions that produced evidence for it. Absorbing a
goal into another marks it `absorbed`, records `absorbed_into`, merges its evidence into
the target and re-points goals that were already absorbed into it (refs are rewritten,
never left dangling). Every mutation emits `goal.created` / `goal.updated` on `server`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import GoalRow, utcnow

if TYPE_CHECKING:
    from auoreo.core.services import Services

STATUSES = ("active", "paused", "done", "dropped", "absorbed")
SETTABLE = frozenset(STATUSES) - {"absorbed"}  # `absorbed` only through `absorb()`
MAX_EVIDENCE = 200


class GoalError(ValueError):
    """A refused goal operation (unknown id, bad status, invalid absorb)."""


def merge_evidence(*lists: list[str]) -> list[str]:
    """Order-preserving union, newest kept when over `MAX_EVIDENCE`."""
    seen: dict[str, None] = {}
    for items in lists:
        for sid in items or []:
            if sid:
                seen[str(sid)] = None
    out = list(seen)
    return out[-MAX_EVIDENCE:]


def goal_data(row: GoalRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "progress": row.progress,
        "evidence_session_ids": list(row.evidence_session_ids or []),
        "session_id": row.session_id,
        "absorbed_into": row.absorbed_into,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class GoalsService:
    def __init__(self, services: Services) -> None:
        self.s = services

    async def list(self, status: str | None = "active", limit: int = 100) -> list[GoalRow]:
        """`status=None` or `"all"` lists every goal; otherwise one status."""
        q = select(GoalRow).order_by(GoalRow.id.desc()).limit(min(max(limit, 1), 1000))
        if status and status != "all":
            if status not in STATUSES:
                raise GoalError(f"unknown status '{status}' (known: {', '.join(STATUSES)})")
            q = q.where(GoalRow.status == status)
        async with self.s.db.session() as s:
            return list((await s.execute(q)).scalars())

    async def get(self, goal_id: int) -> GoalRow | None:
        async with self.s.db.session() as s:
            return await s.get(GoalRow, goal_id)

    async def require(self, goal_id: int) -> GoalRow:
        row = await self.get(goal_id)
        if row is None:
            raise GoalError(f"goal {goal_id} not found")
        return row

    async def create(self, title: str, description: str = "", *, session_id: str | None = None) -> GoalRow:
        title = title.strip()
        if not title:
            raise GoalError("title must not be empty")
        row = GoalRow(
            title=title[:300],
            description=description[:5000],
            status="active",
            progress=0.0,
            evidence_session_ids=[session_id] if session_id else [],
            session_id=session_id,
        )
        async with self.s.db.session() as s:
            s.add(row)
            await s.commit()
        await self._emit("goal.created", row)
        return row

    async def update(
        self,
        goal_id: int,
        *,
        status: str | None = None,
        progress: float | None = None,
        title: str | None = None,
        description: str | None = None,
        evidence_session: str | None = None,
    ) -> GoalRow:
        if status is not None and status not in SETTABLE:
            raise GoalError(
                f"status must be one of {', '.join(sorted(SETTABLE))} (use absorb_into to absorb)"
            )
        if progress is not None and not 0.0 <= progress <= 1.0:
            raise GoalError("progress must be between 0 and 1")
        async with self.s.db.session() as s:
            row = await s.get(GoalRow, goal_id)
            if row is None:
                raise GoalError(f"goal {goal_id} not found")
            if row.status == "absorbed" and status is not None:
                raise GoalError(f"goal {goal_id} was absorbed into goal {row.absorbed_into}; update that one")
            if status is not None:
                row.status = status
            if progress is not None:
                row.progress = float(progress)
            if title is not None and title.strip():
                row.title = title.strip()[:300]
            if description is not None:
                row.description = description[:5000]
            if evidence_session:
                row.evidence_session_ids = merge_evidence(row.evidence_session_ids, [evidence_session])
            row.updated_at = utcnow()
            await s.commit()
        await self._emit("goal.updated", row)
        return row

    async def absorb(self, goal_id: int, into: int, *, evidence_session: str | None = None) -> GoalRow:
        """Mark `goal_id` absorbed into `into`; returns the (updated) target goal."""
        if goal_id == into:
            raise GoalError("a goal cannot absorb itself")
        async with self.s.db.session() as s:
            src = await s.get(GoalRow, goal_id)
            dst = await s.get(GoalRow, into)
            if src is None or dst is None:
                raise GoalError(f"goal {goal_id if src is None else into} not found")
            if src.status == "absorbed":
                raise GoalError(f"goal {goal_id} is already absorbed into goal {src.absorbed_into}")
            if dst.status == "absorbed":
                raise GoalError(
                    f"goal {into} is absorbed into goal {dst.absorbed_into}; absorb into that one"
                )
            now = utcnow()
            extra = [evidence_session] if evidence_session else []
            dst.evidence_session_ids = merge_evidence(
                dst.evidence_session_ids, src.evidence_session_ids, extra
            )
            dst.updated_at = now
            src.status = "absorbed"
            src.absorbed_into = dst.id
            src.updated_at = now
            # rewrite refs: goals previously absorbed into `src` now point at `dst`
            prior = (await s.execute(select(GoalRow).where(GoalRow.absorbed_into == src.id))).scalars()
            moved = []
            for row in prior:
                row.absorbed_into = dst.id
                row.updated_at = now
                moved.append(row)
            await s.commit()
        await self._emit("goal.updated", src)
        for row in moved:
            await self._emit("goal.updated", row)
        await self._emit("goal.updated", dst)
        return dst

    async def _emit(self, type_: str, row: GoalRow) -> None:
        data = goal_data(row)
        data.pop("created_at")
        data.pop("updated_at")
        await self.s.emit("server", type_, data)


def goals_of(services: Services) -> GoalsService:
    """The started service (`services.ext["goals"]`) or a fresh one (CLI, tests)."""
    svc = services.ext.get("goals")
    return svc if isinstance(svc, GoalsService) else GoalsService(services)


async def start(services: Services) -> GoalsService:
    return GoalsService(services)
