"""POST idempotency: a key maps to the first result for 24h (spec §24.7)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from auoreo.core.db import Database, IdempotencyRow, utcnow

TTL = timedelta(hours=24)


def valid_key(key: str) -> bool:
    try:
        uuid.UUID(key)
    except ValueError:
        return False
    return True


class Idempotency:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def lookup(self, key: str) -> dict[str, Any] | None:
        async with self.db.session() as s:
            await s.execute(delete(IdempotencyRow).where(IdempotencyRow.created_at < utcnow() - TTL))
            await s.commit()
            row = await s.get(IdempotencyRow, key)
            return dict(row.result) if row else None

    async def store(self, key: str, result: dict[str, Any]) -> dict[str, Any]:
        """Store the first result; on a race, return the winner's result."""
        async with self.db.session() as s:
            s.add(IdempotencyRow(key=key, result=result))
            try:
                await s.commit()
                return result
            except IntegrityError:
                await s.rollback()
        existing = await self.lookup(key)
        return existing or result
