"""SQLAlchemy 2 async engine + ORM tables for `state.db` (WAL, single writer)."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

SCHEMA_VERSION = 3  # 2: sessions.archived, usage, messages_archive (M3); 3: automation + teams (M5)
ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
BASELINE_REVISION = "0001"  # the M2 schema; pre-Alembic databases are stamped here


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
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
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


class MessageArchiveRow(Base):
    """Messages replaced by compaction (soft-archive, still searchable). Schema v2."""

    __tablename__ = "messages_archive"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    compaction_id: Mapped[str] = mapped_column(String(64))
    idx: Mapped[int] = mapped_column(Integer)
    message: Mapped[dict[str, Any]]
    archived_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_messages_archive_session", "session_id", "compaction_id"),)


class UsageRow(Base):
    """Per model call token/cost ledger (spec §20.2). Schema v2."""

    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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


class DeliveryRow(Base):
    """Claim-at-most-once outbox for webhook/job/team results (spec §16.7). Schema v3."""

    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))  # webhook | job | team
    source: Mapped[str] = mapped_column(String(128))  # route / job name / team id
    external_id: Mapped[str] = mapped_column(String(128))  # provider delivery id or run id
    status: Mapped[str] = mapped_column(String(32), default="pending")
    """pending → delivering → delivered | failed | unknown (crash after claim: never retried)."""
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str] = mapped_column(String(64), default="log")
    payload: Mapped[dict[str, Any]]
    result: Mapped[dict[str, Any]]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (
        Index("ix_deliveries_dedupe", "kind", "source", "external_id", unique=True),
        Index("ix_deliveries_status", "status", "created_at"),
    )


class JobRunRow(Base):
    """One scheduled-job firing. Schema v3."""

    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(64), index=True)
    scheduled_for: Mapped[datetime] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|running|ok|failed|skipped
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catch_up: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    __table_args__ = (Index("ix_job_runs_dedupe", "job", "scheduled_for", unique=True),)


class TeamRow(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(1000), default="")
    lead_session_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TeamMemberRow(Base):
    __tablename__ = "team_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|done|failed|stopped
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_team_members_name", "team_id", "name", unique=True),)


class MailboxRow(Base):
    """Append-only agent-to-agent mail; only `read` may be updated (spec §16.4). Schema v3."""

    __tablename__ = "mailbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    recipient: Mapped[str] = mapped_column(String(64), index=True)  # member name or "*"
    sender: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32), default="text")
    summary: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(String(100_000))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class KanbanTaskRow(Base):
    """Kanban-lite board (spec §16.6): atomic claim via UPDATE … WHERE status='ready'."""

    __tablename__ = "kanban_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board: Mapped[str] = mapped_column(String(64), index=True, default="default")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(20_000), default="")
    status: Mapped[str] = mapped_column(String(32), default="ready")  # ready|claimed|blocked|done
    worker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class KanbanCommentRow(Base):
    __tablename__ = "kanban_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("kanban_tasks.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(20_000))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SchemaTooNewError(RuntimeError):
    pass


def alembic_config(url: str) -> Any:
    """Programmatic Alembic config (no .ini needed) pointing at the packaged scripts."""
    from alembic.config import Config

    cfg = Config(stdout=sys.stdout)  # resolved per call so CLI output can be captured
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _stored_version(path: Path) -> int | None:
    if not path.is_file():
        return None
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:  # brand new / no schema_meta yet
        return None
    finally:
        con.close()
    return int(row[0]) if row else None


def migrate(path: Path | str, revision: str = "head") -> None:
    """`alembic upgrade` on a file database (sync; run it in a thread from async code).

    A pre-Alembic (M2) database has tables but no `alembic_version`: stamp the baseline
    first so only the newer revisions run."""
    from alembic import command
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{path}"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            cfg = alembic_config(url)
            cfg.attributes["connection"] = conn
            tables = set(inspect(conn).get_table_names())
            if tables and "alembic_version" not in tables:
                command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, revision)
    finally:
        engine.dispose()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = None if str(path) == ":memory:" else Path(path)
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
        if self.path is None:  # tests: no migration history needed
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            stored = await asyncio.to_thread(_stored_version, self.path)
            if stored is not None and stored > SCHEMA_VERSION:  # refuse before touching it
                raise SchemaTooNewError(
                    f"state.db schema v{stored} is newer than this build (v{SCHEMA_VERSION})"
                )
            await asyncio.to_thread(migrate, self.path)
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
    "SchemaTooNewError",
    "EventRow",
    "DeliveryRow",
    "IdempotencyRow",
    "JobRunRow",
    "KanbanCommentRow",
    "KanbanTaskRow",
    "MailboxRow",
    "MessageArchiveRow",
    "MessageRow",
    "SessionRow",
    "TeamMemberRow",
    "TeamRow",
    "UsageRow",
    "alembic_config",
    "migrate",
    "utcnow",
]
