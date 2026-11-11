"""Read-only loading for `auoreo stats --token-economy` (`core.token_report`).

Everything comes from `state.db`: main-turn `usage` rows (`outcome == "ok"`, no
`parent_session_id`; the Wave 1 definition), a handful of event types, the sessions those rows
belong to, their current `messages` rows and the bandit outcomes. Nothing is written and no
model is called.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from auoreo.core.db import BanditOutcomeRow, Database, EventRow, MessageRow, SessionRow, UsageRow, utcnow

EVENT_TYPES = (
    "turn.summary",
    "message.created",
    "run.started",
    "cache.prefix_changed",
    "compaction.completed",
)
OUTCOMES_PER_CONTEXT = 200


@dataclass(frozen=True)
class Call:
    id: int
    session_id: str
    run_id: str
    model: str
    prompt: int
    completion: int
    cached: int
    cache_write: int
    cost: float
    at: datetime

    @property
    def hit(self) -> float:
        return min(1.0, self.cached / self.prompt) if self.prompt > 0 else 0.0


@dataclass
class Turn:
    run_id: str
    session_id: str
    calls: list[Call] = field(default_factory=list)
    text: str | None = None
    asked_at: datetime | None = None
    agent: str = ""
    summary: dict[str, Any] | None = None

    @property
    def cost(self) -> float:
        return sum(c.cost for c in self.calls)


@dataclass
class Data:
    since: datetime
    calls: list[Call]
    turns: dict[str, Turn]
    sessions: dict[str, SessionRow]
    prefix_changes: int
    compactions: list[dict[str, Any]]
    outcomes: dict[str, list[dict[str, Any]]]
    _db: Database

    async def messages(self, session_id: str) -> list[tuple[dict[str, Any], datetime]]:
        """The session's current `messages` rows, oldest first (loaded on demand)."""
        async with self._db.session() as s:
            q = select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.idx)
            return [(r.message, r.created_at) for r in (await s.execute(q)).scalars()]


def _call(r: UsageRow) -> Call:
    return Call(
        r.id,
        r.session_id,
        r.run_id,
        r.model,
        int(r.prompt_tokens or 0),
        int(r.completion_tokens or 0),
        int(r.cached_tokens or 0),
        int(r.cache_write_tokens or 0),
        float(r.cost_usd or 0.0),
        r.created_at,
    )


async def _calls(db: Database, since: datetime) -> list[Call]:
    async with db.session() as s:
        q = (
            select(UsageRow)
            .where(
                UsageRow.created_at >= since,
                UsageRow.outcome == "ok",
                UsageRow.parent_session_id.is_(None),
            )
            .order_by(UsageRow.id)
        )
        return [_call(r) for r in (await s.execute(q)).scalars()]


async def _sessions(db: Database, ids: set[str]) -> dict[str, SessionRow]:
    if not ids:
        return {}
    async with db.session() as s:
        q = select(SessionRow).where(SessionRow.id.in_(sorted(ids)))
        return {r.id: r for r in (await s.execute(q)).scalars()}


async def _events(db: Database, since: datetime) -> list[EventRow]:
    async with db.session() as s:
        q = (
            select(EventRow)
            .where(EventRow.created_at >= since, EventRow.type.in_(EVENT_TYPES))
            .order_by(EventRow.seq)
        )
        return list((await s.execute(q)).scalars())


async def _outcomes(db: Database) -> dict[str, list[dict[str, Any]]]:
    """The last `OUTCOMES_PER_CONTEXT` outcome signals per bandit context, newest first."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    async with db.session() as s:
        q = select(BanditOutcomeRow.context_key, BanditOutcomeRow.signals).order_by(
            BanditOutcomeRow.id.desc()
        )
        for key, signals in (await s.execute(q)).all():
            if len(out[key]) < OUTCOMES_PER_CONTEXT:
                out[key].append(dict(signals or {}))
    return dict(out)


def _summary(turn: Turn, e: EventRow) -> None:
    turn.summary = e.data or {}


def _started(turn: Turn, e: EventRow) -> None:
    turn.agent = str((e.data or {}).get("agent") or "")


def _created(turn: Turn, e: EventRow) -> None:
    data = e.data or {}
    if data.get("role") == "user" and turn.text is None:
        turn.text, turn.asked_at = str(data.get("content") or ""), e.created_at


_TURN_EVENTS = {"turn.summary": _summary, "run.started": _started, "message.created": _created}


def _attach(
    turns: dict[str, Turn], events: list[EventRow], main: set[str]
) -> tuple[int, list[dict[str, Any]]]:
    prefix_changes, compactions = 0, []
    for e in events:
        if e.aggregate_id not in main:
            continue
        if e.type == "cache.prefix_changed":
            prefix_changes += 1
            continue
        if e.type == "compaction.completed":
            compactions.append(dict(e.data or {}))
            continue
        turn = turns.get(str((e.data or {}).get("run_id", "")))
        handler = _TURN_EVENTS.get(e.type)
        if turn is not None and handler is not None:
            handler(turn, e)
    return prefix_changes, compactions


async def load(db: Database, days: int) -> Data:
    since = utcnow() - timedelta(days=days)
    calls = await _calls(db, since)
    sessions = await _sessions(db, {c.session_id for c in calls})
    main = {sid for sid, row in sessions.items() if row.parent_id is None}
    calls = [c for c in calls if c.session_id in main]
    turns: dict[str, Turn] = {}
    for c in calls:
        turns.setdefault(c.run_id, Turn(c.run_id, c.session_id)).calls.append(c)
    prefix_changes, compactions = _attach(turns, await _events(db, since), main)
    return Data(since, calls, turns, sessions, prefix_changes, compactions, await _outcomes(db), db)


__all__ = ["Call", "Data", "Turn", "load"]
