---
name: sqlalchemy
description: SQLite (and later Postgres) persistence with SQLAlchemy 2.0 — declarative models, sync/async sessions, migrations with Alembic. Use when writing agent_core database/event/session stores in this repo.
---

# SQLAlchemy 2.0 — storage layer (agent_core)

Docs: https://docs.sqlalchemy.org/en/20 · Context7: `/websites/sqlalchemy_en_20` (Core) + `/websites/sqlalchemy_en_20_orm` (ORM, incl. asyncio ext) + `/websites/alembic_sqlalchemy_en` (Alembic). This repo: SQLite first, Postgres+pgvector as an adapter (plan.md Part E).

## Declarative models (2.0 style — Mapped + mapped_column)

```python
from sqlalchemy import ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(primary_key=True)          # "evt_..." monotonic
    type: Mapped[str] = mapped_column(index=True)
    seq: Mapped[int] = mapped_column()                          # per-aggregate sequence
    aggregate_id: Mapped[str] = mapped_column(index=True)
    data: Mapped[dict] = mapped_column(JSON)                    # event payload
    created_at: Mapped[int] = mapped_column(Integer)            # epoch ms

class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str | None]
    agent_id: Mapped[str | None]
    archived: Mapped[bool] = mapped_column(default=False)
```

- Use snake_case column names (no string redefinition needed); SQLAlchemy 2.0 maps `Mapped[X]` directly.
- Prefer `select(...)` over legacy `.query` (2.0 API), `session.get(Model, id)` over `.query.get`.

## Sessions

```python
engine = create_engine("sqlite+pysqlite:///./agent.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)

with SessionLocal() as session:
    row = session.execute(select(EventRow).filter_by(aggregate_id=sess_id).order_by(EventRow.seq)).scalars().all()
```

- In async handlers use `async_sessionmaker` + `create_async_engine("sqlite+aiosqlite:///...")`; never block the event loop with sync DB calls inside FastAPI/SSE (plan.md Part C).

```python
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine

# For async, declare the Base above as `class Base(AsyncAttrs, DeclarativeBase)` → `await obj.awaitable_attrs.rel`
engine = create_async_engine("sqlite+aiosqlite:///./agent.db")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async with SessionLocal() as session:
    async with session.begin():               # commits on exit, rolls back on error
        session.add(EventRow(...))
    rows = (await session.scalars(select(EventRow).filter_by(aggregate_id=sess_id))).all()
# at shutdown (FastAPI lifespan): await engine.dispose()
```

- Async gotcha: lazy loading triggers implicit IO, which raises under `AsyncSession`. Set `expire_on_commit=False` so attributes stay readable after `commit()`, eager-load relationships with `selectinload(...)`, or use `awaitable_attrs`.
- One short-lived `Session` per operation (per request / per event batch); commit explicitly. A single shared long-lived session across requests causes stale identity map bugs.

## Migrations (Alembic)

```bash
uv add --dev alembic
uv run alembic init -t async src/agent_core/alembic   # async template (works with aiosqlite/asyncpg)
# configure env.py: target_metadata = Base.metadata from agent_core; render_as_batch=True
uv run alembic revision --autogenerate -m "add events table"
uv run alembic upgrade head
```

- Set `render_as_batch=True` in `context.configure(...)` in `env.py`. SQLite can't `ALTER` most column/constraint changes, so autogenerate has to emit `batch_alter_table` (copy-and-move) operations. Postgres is unaffected.
- Migrations live in `agent_core/alembic/` (plan.md Part D). Every schema change = new revision; `upgrade head` runs in CI and at deploy.
- Events table gets a `schema_version` guard so the app can detect "DB newer than code" and refuse to start (plan.md Part E).

## Patterns for this repo

- **Event store = source of truth** (plan.md Part C.3): the `events` table is appended-only; `seq` is the per-aggregate monotonic id used for SSE replay (`Last-Event-ID`, see `fastapi` skill). Do not edit/delete rows — emit new events.
- Sessions/messages are **projections**: rebuild from events on demand, or cache as rows updated by a projector. Keep the projector logic in `agent_core/history.py`.
- Adapter interface in `agent_core/database.py` (or `agent_memory/store.py`): `sqlite` / `pgvector` implementations behind the same interface so Postgres is a config change (plan.md Part E).
- JSON payloads: use the JSON column type; keep event `data` Pydantic-validated (`agent_schema`) before inserting so replay yields typed events.