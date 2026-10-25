"""Alembic helpers for `state.db`, serialised against concurrent initialisers.

Init race (W1-E): pysqlite does not wrap DDL in a transaction, so while one process runs the
`0001` baseline, a second one sees `schema_meta` & co. but an empty/absent `alembic_version`
and runs `0001` again → "table schema_meta already exists". Alembic's `context` proxy is also
process-global, so two threads migrating at once break each other. `migrate()` therefore holds
an in-process lock plus an exclusive OS lock on `<state.db>.lock` and re-reads the state inside
it; the loser then finds the file at head and does nothing.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.crypto_columns import DbKeyring

ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
BASELINE_REVISION = "0001"  # the M2 schema; pre-Alembic databases are stamped here
_PROCESS_LOCK = threading.Lock()


class SchemaTooNewError(RuntimeError):
    pass


def alembic_config(url: str) -> Any:
    """Programmatic Alembic config (no .ini needed) pointing at the packaged scripts."""
    from alembic.config import Config

    cfg = Config(stdout=sys.stdout)  # resolved per call so CLI output can be captured
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@contextmanager
def migrate_lock(path: Path) -> Iterator[None]:
    """Exclusive across threads and processes for one database file (blocking)."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK, lock_path.open("a+b") as fh:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ~10 s; keep waiting
                    continue
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def stored_version(path: Path, keyring: DbKeyring | None = None) -> int | None:
    if not path.is_file():
        return None
    if keyring is not None:
        from auoreo.core import sqlcipher

        con = sqlcipher.sync_connect(path, keyring.ensure())
    else:
        con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    except Exception as exc:  # brand new / no schema_meta yet
        if "no such table" in str(exc):
            return None
        raise
    finally:
        con.close()
    return int(row[0]) if row else None


def migrate(
    path: Path | str,
    revision: str = "head",
    *,
    keyring: DbKeyring | None = None,
    max_version: int | None = None,
) -> None:
    """`alembic upgrade` on a file database (sync; run it in a thread from async code).

    A pre-Alembic (M2) database has tables but no `alembic_version`: stamp the baseline
    first so only the newer revisions run. `keyring` = SQLCipher file. `max_version` refuses
    (SchemaTooNewError) when the stored schema is newer than this build, before touching it."""
    from alembic import command
    from sqlalchemy import create_engine, inspect

    path = Path(path)
    with migrate_lock(path):
        stored = stored_version(path, keyring)
        if max_version is not None and stored is not None and stored > max_version:
            raise SchemaTooNewError(f"state.db schema v{stored} is newer than this build (v{max_version})")
        url = f"sqlite:///{path}"
        if keyring is not None:
            from auoreo.core import sqlcipher

            engine = sqlcipher.sync_engine(path, keyring)
        else:
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
