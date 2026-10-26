"""SQLAlchemy 2 async engine + ORM tables for `state.db` (WAL, single writer)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DDL,
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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from auoreo.core.db_migrate import (  # re-exported: older imports use auoreo.core.db
    ALEMBIC_DIR,
    BASELINE_REVISION,
    SchemaTooNewError,
    alembic_config,
    migrate,
)

if TYPE_CHECKING:
    from auoreo.core.crypto_columns import ColumnCipher

SCHEMA_VERSION = 7  # 2: sessions.archived, usage, messages_archive (M3); 3: automation + teams (M5)
# 4: goals, suggestions (M3–M5 gaps); 5: blackboard, bandit, usage latency, audit triggers (M6)
# 6: usage cache_write/reasoning tokens, generation id, deployment, tags (I2)
# 7 (I3, revision 0007_teams_i3): team_members agent_type/model/color/cwd, usage attribution


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
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # v5
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)  # v5: ok|error code
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)  # v6 (I2)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)  # v6
    generation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # v6
    deployment: Mapped[str | None] = mapped_column(String(255), nullable=True)  # v6: budgets
    tags: Mapped[str | None] = mapped_column(String(512), nullable=True)  # v6: comma-joined
    parent_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # v7 (I3)
    subagent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # v7
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)  # v7
    task_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # v7


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


# The audit table is append-only at the SQL level too (spec §19.13): hash ≠ immutability.
AUDIT_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
)
for _sql in AUDIT_TRIGGERS:
    event.listen(AuditRow.__table__, "after_create", DDL(_sql))  # type: ignore[no-untyped-call]


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
    subagent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # v7: sa-{n}-{hex8}
    agent_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # v7 (spec §16.4)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)  # v7
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)  # v7
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # v7
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
    profile: Mapped[str | None] = mapped_column(String(64), nullable=True)  # v7: worker agent


class KanbanCommentRow(Base):
    __tablename__ = "kanban_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("kanban_tasks.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(20_000))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GoalRow(Base):
    """Long-running user goals (spec §2.3, §24.10): `get/create/update_goal`. Schema v4."""

    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(String(5000), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    """active | paused | done | dropped | absorbed"""
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_session_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    absorbed_into: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class SuggestionRow(Base):
    """Proactive suggestions (spec §16, §24.10): consent-first, never auto-executed. Schema v4."""

    __tablename__ = "suggestions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), default="info")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(String(4000), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    """pending | accepted | denied | expired | tombstoned"""
    deliver: Mapped[str] = mapped_column(String(64), default="origin")
    origin_session: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    __table_args__ = (
        Index("ix_suggestions_status", "status", "created_at"),
        Index("ix_suggestions_dedupe", "dedupe_key"),
    )


class ArtifactRow(Base):
    """Team blackboard (spec L1024): small keyed artifacts, ≤64 KiB each; the mailbox stays
    control-plane only. Schema v5."""

    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(String(65_536))
    producer: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_artifacts_key", "team_id", "key", unique=True),)


class BanditArmRow(Base):
    """Contextual epsilon-greedy arm statistics (spec §12.5). Schema v5."""

    __tablename__ = "bandit_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_key: Mapped[str] = mapped_column(String(128))
    arm_key: Mapped[str] = mapped_column(String(255))
    arm: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pulls: Mapped[int] = mapped_column(Integer, default=0)
    reward_sum: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_bandit_policy_arm", "context_key", "arm_key", unique=True),)


class BanditOutcomeRow(Base):
    """One rewarded turn (spec §12.5). Schema v5."""

    __tablename__ = "bandit_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    context_key: Mapped[str] = mapped_column(String(128))
    arm_key: Mapped[str] = mapped_column(String(255))
    reward: Mapped[float] = mapped_column(Float, default=0.0)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_bandit_outcomes_context", "context_key", "created_at"),)


def _default_cipher(path: Path | None, mode: str | None) -> ColumnCipher:
    """Encryption mode + key ring from `Settings()` (env + settings.json); the key file lives
    next to the database file."""
    from auoreo.core.crypto_columns import ColumnCipher, DbKeyring

    try:
        from auoreo.core.settings import Settings

        st = Settings()
        home, policy, mode = (path.parent if path else st.home), st.keyring, mode or st.db_encryption
    except Exception:  # unreadable settings never block opening the database
        home, policy, mode = (path.parent if path else Path.cwd()), "allow-file", mode or "off"
    return ColumnCipher(mode, DbKeyring(home, policy))


class Database:
    def __init__(
        self, path: Path | str, *, encryption: str | None = None, cipher: ColumnCipher | None = None
    ) -> None:
        self.path = None if str(path) == ":memory:" else Path(path)
        self.cipher = cipher or _default_cipher(self.path, encryption)
        """Column cipher (W1-E): every reader of `events.data` / `audit.detail` decrypts through it."""
        url = "sqlite+aiosqlite:///:memory:" if str(path) == ":memory:" else f"sqlite+aiosqlite:///{path}"
        if self.sqlcipher:
            from auoreo.core import sqlcipher

            assert self.path is not None
            self.engine: AsyncEngine = sqlcipher.async_engine(self.path, self.cipher.keyring)
        else:
            self.engine = create_async_engine(url)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    @property
    def sqlcipher(self) -> bool:
        return self.cipher.mode == "sqlcipher" and self.path is not None

    async def _preflight(self) -> None:
        """Refuse a sqlcipher mode that cannot work; make sure a `columns` key exists."""
        from auoreo.core import sqlcipher

        if self.sqlcipher:
            assert self.path is not None
            if not sqlcipher.available():
                raise RuntimeError(
                    "db_encryption=sqlcipher needs the `sqlcipher` extra (sqlcipher3); install it "
                    "or use db_encryption=columns"
                )
            if self.path.is_file() and not sqlcipher.is_encrypted_file(self.path):
                raise RuntimeError(
                    f"{self.path} is plaintext; stop the server and run `auoreo db encrypt` "
                    "(or use db_encryption=columns)"
                )
        if self.cipher.mode in {"columns", "sqlcipher"} and self.path is not None:
            await asyncio.to_thread(self.cipher.ensure_key)

    async def init(self) -> None:
        await self._preflight()
        if self.path is None:  # tests: no migration history needed
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            keyring = self.cipher.keyring if self.sqlcipher else None
            await asyncio.to_thread(migrate, self.path, keyring=keyring, max_version=SCHEMA_VERSION)
        async with self.session() as s:  # OR IGNORE: a concurrent initialiser may win the insert
            stmt = sqlite_insert(Meta).values(key="schema_version", value=str(SCHEMA_VERSION))
            await s.execute(stmt.on_conflict_do_nothing())
            await s.commit()
            row = await s.get(Meta, "schema_version")
            if row is not None and int(row.value) > SCHEMA_VERSION:
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
    "ALEMBIC_DIR",
    "BASELINE_REVISION",
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
