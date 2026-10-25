"""W1-E: concurrent first-time init of the same new state.db ("table schema_meta already exists").

Cause: pysqlite runs DDL outside a transaction, so a second initialiser saw the baseline's
tables but no `alembic_version` row yet and ran `0001` again; Alembic's process-global
`context` also breaks two threads migrating at once. `migrate()` now serialises on a lock."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import sqlite3
from pathlib import Path

from auoreo.core.db import SCHEMA_VERSION, Database


async def _init(path: Path) -> str:
    db = Database(path)
    try:
        await db.init()
        return "ok"
    except Exception as exc:  # report, don't raise: we want every result
        return f"{type(exc).__name__}: {exc}"
    finally:
        await db.close()


def _state(path: Path) -> tuple[str, str]:
    con = sqlite3.connect(path)
    try:
        rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        ver = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    finally:
        con.close()
    return rev, ver


async def test_concurrent_inits_in_one_process(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    results = await asyncio.gather(*(_init(path) for _ in range(4)))
    assert results == ["ok"] * 4
    assert _state(path)[1] == str(SCHEMA_VERSION)


def _child(path: str, q: mp.Queue) -> None:  # type: ignore[type-arg]
    import contextlib
    import io
    import logging

    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()):
        q.put(asyncio.run(_init(Path(path))))


def test_concurrent_inits_across_processes(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    for i in range(3):
        path = tmp_path / f"state{i}.db"
        q = ctx.Queue()
        procs = [ctx.Process(target=_child, args=(str(path), q)) for _ in range(3)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        assert [q.get(timeout=5) for _ in procs] == ["ok"] * 3
        assert _state(path)[1] == str(SCHEMA_VERSION)
