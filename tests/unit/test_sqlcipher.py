"""W1-E SQLCipher whole-file mode for the async state.db path (skipped without sqlcipher3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import sqlcipher
from auoreo.core.audit import AuditLog
from auoreo.core.bus import EventBus
from auoreo.core.crypto_columns import ColumnCipher, DbKeyring
from auoreo.core.db import Database, IdempotencyRow
from auoreo.core.event_store import EventStore

pytestmark = pytest.mark.skipif(not sqlcipher.available(), reason="sqlcipher3 not installed")
MARK = "sqlcipher-marker-7"


def _db(home: Path, mode: str = "sqlcipher") -> Database:
    return Database(home / "state.db", cipher=ColumnCipher(mode, DbKeyring(home)))


def test_check_reports_cipher_version() -> None:
    ok, info = sqlcipher.check()
    assert ok and info.startswith("SQLCipher ")


async def test_fresh_encrypted_state_db(home: Path) -> None:
    db = _db(home)
    await db.init()
    await EventStore(db, EventBus()).append("s", "t", {"text": MARK})
    await AuditLog(db).record(actor="a", action="x", detail={"text": MARK})
    async with db.session() as s:  # sqlalchemy.exc translation still works (IntegrityError)
        from sqlalchemy.exc import IntegrityError

        s.add(IdempotencyRow(key="k", result={}))
        await s.commit()
        s.add(IdempotencyRow(key="k", result={}))
        with pytest.raises(IntegrityError):
            await s.commit()
    await db.close()
    assert sqlcipher.is_encrypted_file(home / "state.db")
    assert MARK.encode() not in (home / "state.db").read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(home / "state.db").execute("SELECT * FROM events").fetchall()
    db = _db(home)
    await db.init()  # reopen + migrate (already at head)
    (ev,) = await EventStore(db, EventBus()).replay()
    assert ev.data == {"text": MARK}
    assert await AuditLog(db).verify() is None
    await db.close()


async def test_plaintext_file_refuses_then_encrypt_converts(home: Path) -> None:
    db = _db(home, "off")
    await db.init()
    await EventStore(db, EventBus()).append("s", "t", {"text": MARK})
    await db.close()
    with pytest.raises(RuntimeError, match="auoreo db encrypt"):
        await _db(home).init()
    r = CliRunner().invoke(app, ["db", "encrypt", "--yes"])
    assert r.exit_code == 0, r.output
    assert sqlcipher.is_encrypted_file(home / "state.db")
    db = _db(home)
    await db.init()
    (ev,) = await EventStore(db, EventBus()).replay()
    assert ev.data == {"text": MARK}
    await db.close()
    r = CliRunner().invoke(app, ["db", "rotate-key"])  # re-keys the file
    assert r.exit_code == 0 and "re-keyed" in r.output, r.output
    keys = DbKeyring(home).load()
    sqlcipher.sync_connect(home / "state.db", keys[:1]).close()  # opens with the new primary only
    dest = home.parent / "bk"
    assert CliRunner().invoke(app, ["db", "backup", str(dest)]).exit_code == 0
    assert sqlcipher.is_encrypted_file(dest / "state.db")
    sqlcipher.sync_connect(dest / "state.db", keys[:1]).close()
