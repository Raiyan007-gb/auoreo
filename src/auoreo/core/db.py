"""SQLAlchemy 2 async engine + ORM tables for `state.db` (WAL, single writer)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store naive UTC in SQLite, always hand back aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime}


class Meta(Base):
    __tablename__ = "schema_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    agent: Mapped[str] = mapped_column(String(64))
    workflow: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="idle")
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class EventRow(Base):
    __tablename__ = "events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aggregate_id: Mapped[str] = mapped_column(String(64))
    aggregate_seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_events_agg", "aggregate_id", "aggregate_seq", unique=True),)


class MessageRow(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    message: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_messages_session", "session_id", "idx", unique=True),)


class AuditRow(Base):
    __tablename__ = "audit"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64))


class IdempotencyRow(Base):
    __tablename__ = "idempotency"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    result: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SchemaTooNewError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path | str) -> None:
        url = "sqlite+aiosqlite:///:memory:" if str(path) == ":memory:" else f"sqlite+aiosqlite:///{path}"
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.session() as s:
            row = await s.get(Meta, "schema_version")
            if row is None:
                s.add(Meta(key="schema_version", value=str(SCHEMA_VERSION)))
                await s.commit()
            elif int(row.value) > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"state.db schema v{row.value} is newer than this build (v{SCHEMA_VERSION})"
                )

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def close(self) -> None:
        await self.engine.dispose()

    async def scalar_count(self, model: type[Base]) -> int:
        async with self.session() as s:
            return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


__all__ = [
    "AuditRow",
    "Database",
    "EventRow",
    "IdempotencyRow",
    "MessageRow",
    "SessionRow",
    "utcnow",
]
