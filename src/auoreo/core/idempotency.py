"""POST idempotency (spec §24.7): claim-at-most-once `pending → done`, 24 h.

`admit(key, route)` inserts a `pending` row atomically (the primary key is the claim), so of
N concurrent requests with one key exactly one is `fresh` and runs; the others see
`in_progress` (→ 409) until the winner calls `complete()`, then `duplicate` with the stored
response. A failed attempt calls `release()` so a retry can run. The state lives in the
row's JSON (`{"_state": "pending"|"done", ...}`) — no schema change.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from auoreo.core.db import Database, IdempotencyRow, utcnow

TTL = timedelta(hours=24)
PENDING_TTL = timedelta(minutes=10)
"""A `pending` claim older than this belonged to a crashed request and may be re-claimed."""

AdmitState = Literal["fresh", "duplicate", "in_progress"]


def valid_key(key: str) -> bool:
    try:
        uuid.UUID(key)
    except ValueError:
        return False
    return True


def row_key(key: str, route: str) -> str:
    """Keys are scoped per route: the same UUID on two endpoints never collides."""
    return hashlib.sha256(f"{route}\n{key}".encode()).hexdigest()


@dataclass(frozen=True)
class Admission:
    state: AdmitState
    key: str
    route: str
    status_code: int = 200
    body: Any = None


class Idempotency:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def admit(self, key: str, route: str) -> Admission:
        rk = row_key(key, route)
        now = utcnow()
        async with self.db.session() as s:
            await s.execute(delete(IdempotencyRow).where(IdempotencyRow.created_at < now - TTL))
            await s.commit()
            s.add(IdempotencyRow(key=rk, result={"_state": "pending", "route": route}, created_at=now))
            try:
                await s.commit()
                return Admission("fresh", key, route)
            except IntegrityError:
                await s.rollback()
            row = await s.get(IdempotencyRow, rk)
            if row is None:  # released between our insert and read: try once more
                s.add(IdempotencyRow(key=rk, result={"_state": "pending", "route": route}, created_at=now))
                try:
                    await s.commit()
                    return Admission("fresh", key, route)
                except IntegrityError:
                    await s.rollback()
                    return Admission("in_progress", key, route)
            result = dict(row.result)
            if result.get("_state") == "done":
                return Admission(
                    "duplicate", key, route, int(result.get("status_code", 200)), result.get("body")
                )
            if row.created_at < now - PENDING_TTL:  # stale claim: take it over (compare-and-set)
                won = await s.execute(
                    update(IdempotencyRow)
                    .where(IdempotencyRow.key == rk, IdempotencyRow.created_at == row.created_at)
                    .values(created_at=now)
                )
                await s.commit()
                if getattr(won, "rowcount", 0) == 1:
                    return Admission("fresh", key, route)
            return Admission("in_progress", key, route)

    async def complete(self, key: str, route: str, status_code: int, body: Any) -> None:
        async with self.db.session() as s:
            await s.execute(
                update(IdempotencyRow)
                .where(IdempotencyRow.key == row_key(key, route))
                .values(result={"_state": "done", "route": route, "status_code": status_code, "body": body})
            )
            await s.commit()

    async def release(self, key: str, route: str) -> None:
        async with self.db.session() as s:
            await s.execute(delete(IdempotencyRow).where(IdempotencyRow.key == row_key(key, route)))
            await s.commit()
