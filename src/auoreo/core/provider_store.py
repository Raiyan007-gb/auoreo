"""`provider_connections` / `provider_models` access (async, `svc.db`). Never touches a key:
rows carry the secret NAME only; the value lives in the secret store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from auoreo.core.db import utcnow
from auoreo.core.db_settings import ProviderConnectionRow, ProviderModelRow
from auoreo.gateway.provider_fetch import ModelInfo

CACHE_TTL = timedelta(hours=1)


async def connections(db: Any) -> list[ProviderConnectionRow]:
    async with db.session() as s:
        return list(
            (await s.execute(select(ProviderConnectionRow).order_by(ProviderConnectionRow.id))).scalars()
        )


async def connection(db: Any, conn_id: int) -> ProviderConnectionRow | None:
    async with db.session() as s:
        row: ProviderConnectionRow | None = await s.get(ProviderConnectionRow, conn_id)
        return row


async def by_provider(db: Any, provider: str) -> list[ProviderConnectionRow]:
    return [c for c in await connections(db) if c.provider == provider]


def _model_rows(conn_id: int, models: Sequence[ModelInfo], now: datetime) -> list[ProviderModelRow]:
    return [
        ProviderModelRow(
            connection_id=conn_id,
            model_id=m.id[:255],
            name=(m.name or m.id)[:255],
            kind=m.kind,
            context_window=m.context_window,
            input_price=m.input_price,
            output_price=m.output_price,
            meta={**m.meta, "capabilities": m.capabilities},
            fetched_at=now,
        )
        for m in models
    ]


async def create(db: Any, values: dict[str, Any], models: Sequence[ModelInfo]) -> ProviderConnectionRow:
    now = utcnow()
    async with db.session() as s:
        row = ProviderConnectionRow(
            **values, status="ok", model_count=len(models), checked_at=now, created_at=now, updated_at=now
        )
        s.add(row)
        await s.flush()
        s.add_all(_model_rows(row.id, models, now))
        await s.commit()
        return row


async def update(
    db: Any, conn_id: int, values: dict[str, Any], models: Sequence[ModelInfo] | None = None
) -> ProviderConnectionRow | None:
    """Apply `values`; `models` (when given) replaces the cached list."""
    now = utcnow()
    async with db.session() as s:
        row: ProviderConnectionRow | None = await s.get(ProviderConnectionRow, conn_id)
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = now
        if models is not None:
            await s.execute(delete(ProviderModelRow).where(ProviderModelRow.connection_id == conn_id))
            s.add_all(_model_rows(conn_id, models, now))
            row.model_count = len(models)
        await s.commit()
        return row


async def remove(db: Any, conn_id: int) -> None:
    async with db.session() as s:
        await s.execute(delete(ProviderModelRow).where(ProviderModelRow.connection_id == conn_id))
        await s.execute(delete(ProviderConnectionRow).where(ProviderConnectionRow.id == conn_id))
        await s.commit()


async def models(
    db: Any, conn_id: int, kind: str | None = None, q: str | None = None
) -> list[ProviderModelRow]:
    stmt = select(ProviderModelRow).where(ProviderModelRow.connection_id == conn_id)
    if kind:
        stmt = stmt.where(ProviderModelRow.kind == kind)
    async with db.session() as s:
        rows = list((await s.execute(stmt.order_by(ProviderModelRow.model_id))).scalars())
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r.model_id.lower() or needle in r.name.lower()]
    return rows


async def model(db: Any, conn_id: int, model_id: str) -> ProviderModelRow | None:
    stmt = select(ProviderModelRow).where(
        ProviderModelRow.connection_id == conn_id, ProviderModelRow.model_id == model_id
    )
    async with db.session() as s:
        found: ProviderModelRow | None = (await s.execute(stmt)).scalars().first()
        return found


def fetched_at(rows: Sequence[ProviderModelRow], conn: ProviderConnectionRow) -> datetime | None:
    return max((r.fetched_at for r in rows), default=None) or conn.checked_at


def stale(conn: ProviderConnectionRow) -> bool:
    return conn.checked_at is None or utcnow() - conn.checked_at > CACHE_TTL


__all__ = [
    "CACHE_TTL",
    "by_provider",
    "connection",
    "connections",
    "create",
    "fetched_at",
    "model",
    "models",
    "remove",
    "stale",
    "update",
]
