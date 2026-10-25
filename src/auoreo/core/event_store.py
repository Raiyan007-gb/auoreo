"""Append-only event log with per-aggregate sequence numbers and replay."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import func, select

from auoreo.core import observability as obs
from auoreo.core.bus import EventBus
from auoreo.core.db import Database, EventRow, utcnow
from auoreo.core.redaction import redact_obj
from auoreo.schema.events import ServerEvent

REPLAY_LIMIT = 500


def _to_event(row: EventRow, data: dict[str, Any]) -> ServerEvent:
    return ServerEvent(
        id=str(row.seq),
        seq=row.seq,
        type=row.type,
        aggregate_id=row.aggregate_id,
        aggregate_seq=row.aggregate_seq,
        data=data,
        created_at=row.created_at,
    )


class EventStore:
    def __init__(self, db: Database, bus: EventBus, redact: bool = True) -> None:
        self.db = db
        self.bus = bus
        self.redact = redact
        self._write_lock = asyncio.Lock()  # single writer

    async def append(self, aggregate_id: str, type_: str, data: dict[str, Any]) -> ServerEvent:
        payload = redact_obj(data) if self.redact else data
        async with self._write_lock, self.db.session() as s:
            last = (
                await s.execute(
                    select(func.max(EventRow.aggregate_seq)).where(EventRow.aggregate_id == aggregate_id)
                )
            ).scalar_one_or_none()
            row = EventRow(
                aggregate_id=aggregate_id,
                aggregate_seq=(last or 0) + 1,
                type=type_,
                data=self.db.cipher.encrypt(payload),  # W1-E: ciphertext at rest in `columns` mode
                created_at=utcnow(),
            )
            s.add(row)
            await s.commit()
            event = _to_event(row, payload)
        self.bus.publish(event)
        obs.count("events_published_total")  # --- W0-D
        return event

    async def replay(
        self, after_seq: int = 0, channels: frozenset[str] | None = None, limit: int = REPLAY_LIMIT
    ) -> list[ServerEvent]:
        q = select(EventRow).where(EventRow.seq > after_seq).order_by(EventRow.seq).limit(limit)
        if channels is not None:
            ids = [c.split(":", 1)[1] for c in channels if c.startswith("session:")]
            if "server" in channels:
                ids.append("server")
            q = q.where(EventRow.aggregate_id.in_(ids))
        async with self.db.session() as s:
            rows = list((await s.execute(q)).scalars())
        return [_to_event(r, self.plain(r.data)) for r in rows]

    def plain(self, stored: Any) -> dict[str, Any]:
        """The one read path for `events.data`: decrypt (if enveloped), then redact."""
        data = self.db.cipher.decrypt(stored)
        return redact_obj(data) if self.redact else data

    async def last_seq(self) -> int:
        async with self.db.session() as s:
            return int((await s.execute(select(func.max(EventRow.seq)))).scalar_one_or_none() or 0)
