"""W1-E `auoreo db backup|restore`: online backup API, key exported only on request."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core.bus import EventBus
from auoreo.core.crypto_columns import ColumnCipher, DbKeyring
from auoreo.core.db import Database
from auoreo.core.event_store import EventStore

runner = CliRunner()


async def _seed(home: Path, mode: str = "columns") -> None:
    db = Database(home / "state.db", cipher=ColumnCipher(mode, DbKeyring(home)))
    await db.init()
    await EventStore(db, EventBus()).append("s", "t", {"v": "hello"})
    await db.close()
    mem = sqlite3.connect(home / "memory.db")
    mem.execute("CREATE TABLE m(x)")
    mem.execute("INSERT INTO m VALUES ('mem')")
    mem.commit()
    mem.close()


async def test_backup_without_key_then_with_key(home: Path, tmp_path: Path) -> None:
    await _seed(home)
    dest = tmp_path / "bk"
    r = runner.invoke(app, ["db", "backup", str(dest)])
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in dest.iterdir()) == ["manifest.json", "memory.db", "state.db"]
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["files"] == ["state.db", "memory.db"] and manifest["key_in_backup"] is False
    con = sqlite3.connect(dest / "state.db")
    assert con.execute("SELECT count(*) FROM events").fetchone() == (1,)
    con.close()
    assert not any("key" in p.name for p in dest.iterdir())

    key = tmp_path / "keys" / "db.key"
    r = runner.invoke(app, ["db", "backup", str(tmp_path / "bk2"), "--with-key", str(key)])
    assert r.exit_code == 0, r.output
    assert key.stat().st_mode & 0o777 == 0o600
    assert key.read_text().strip() == (home / ".db.key").read_text().strip()


async def test_restore_needs_yes_and_brings_back_data_and_key(home: Path, tmp_path: Path) -> None:
    await _seed(home)
    key = tmp_path / "db.key"
    assert runner.invoke(app, ["db", "backup", str(tmp_path / "bk"), "--with-key", str(key)]).exit_code == 0
    (home / "state.db").unlink()
    (home / ".db.key").unlink()
    r = runner.invoke(app, ["db", "restore", str(tmp_path / "bk")])
    assert r.exit_code == 2 and "--yes" in r.output
    r = runner.invoke(app, ["db", "restore", str(tmp_path / "bk"), "--yes", "--key", str(key)])
    assert r.exit_code == 0, r.output
    db = Database(home / "state.db", cipher=ColumnCipher("off", DbKeyring(home)))
    await db.init()
    (ev,) = await EventStore(db, EventBus()).replay()
    assert ev.data == {"v": "hello"}
    await db.close()


def test_backup_with_key_refuses_when_no_key(home: Path, tmp_path: Path) -> None:
    r = runner.invoke(app, ["db", "backup", str(tmp_path / "bk"), "--with-key", str(tmp_path / "k")])
    assert r.exit_code != 0
    assert not (tmp_path / "k").exists()
