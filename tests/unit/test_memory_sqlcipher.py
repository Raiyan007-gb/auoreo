"""F6: memory.db under db_encryption=sqlcipher (skipped without sqlcipher3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import sqlcipher
from auoreo.core.crypto_columns import DbKeyError, DbKeyring
from auoreo.memory.store import MemoryStore

pytestmark = pytest.mark.skipif(not sqlcipher.available(), reason="sqlcipher3 not installed")
MARK = "zanzibar-quokka-memory-marker"
VEC = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def test_encrypted_store_has_no_plaintext_and_searches(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_DB_ENCRYPTION", "sqlcipher")
    path = home / "memory.db"
    store = MemoryStore(path, dims=8)
    assert store.encrypted
    assert store.vec_enabled, "sqlite-vec should load on a sqlcipher3 connection"
    fid, _ = store.add_fact(f"The user keeps a {MARK} on the desk", "user", 0.5, VEC, "m")
    store.db.commit()
    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert store.fts("fact", "quokka", 5) == [fid]  # FTS5 works under SQLCipher
    assert store.knn("fact", VEC, 5) == [fid]  # vec0 works under SQLCipher
    store.close()
    assert sqlcipher.is_encrypted_file(path)
    blob = path.read_bytes() + (
        Path(str(path) + "-wal").read_bytes() if Path(str(path) + "-wal").exists() else b""
    )
    assert MARK.encode() not in blob and b"quokka" not in blob
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT * FROM facts").fetchall()
    store = MemoryStore(path, dims=8)  # reopen with the ring
    assert store.fts("fact", "zanzibar", 5) == [fid]
    store.close()
    with pytest.raises(DbKeyError, match="db_encryption=sqlcipher"):
        MemoryStore(path, dims=8, encryption="off")  # encrypted file, plain mode → clear error


def test_vec_failure_falls_back_to_fts(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sqlite_vec.load", lambda con: (_ for _ in ()).throw(RuntimeError("no ext")))
    store = MemoryStore(home / "memory.db", dims=8, encryption="sqlcipher")
    assert store.encrypted and not store.vec_enabled
    fid, _ = store.add_fact(f"{MARK} fallback", "user", 0.5, VEC, "m")
    assert store.fts("fact", "quokka", 5) == [fid] and store.knn("fact", VEC, 5) == []
    store.close()


def test_plaintext_refused_then_encrypt_rotate_backup(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = home / "memory.db"
    store = MemoryStore(path, dims=8, encryption="off")
    fid, _ = store.add_fact(f"The user keeps a {MARK} on the desk", "user", 0.5, VEC, "m")
    store.db.commit()
    store.close()
    assert not sqlcipher.is_encrypted_file(path)
    monkeypatch.setenv("AUOREO_DB_ENCRYPTION", "sqlcipher")
    with pytest.raises(DbKeyError, match="auoreo db encrypt"):
        MemoryStore(path, dims=8)

    r = CliRunner().invoke(app, ["db", "encrypt", "--yes"])
    assert r.exit_code == 0 and "memory.db encrypted" in r.output, r.output
    assert sqlcipher.is_encrypted_file(path) and MARK.encode() not in path.read_bytes()
    assert list(home.glob("memory.db.plain-*.bak"))
    store = MemoryStore(path, dims=8)
    assert store.fts("fact", "quokka", 5) == [fid] and store.knn("fact", VEC, 5) == [fid]
    version = store.db.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 1  # migrations level survived sqlcipher_export
    store.close()

    r = CliRunner().invoke(app, ["db", "rotate-key"])
    assert r.exit_code == 0 and "memory.db re-keyed" in r.output, r.output
    keys = DbKeyring(home).load()
    sqlcipher.sync_connect(path, keys[:1]).close()  # opens with the new primary alone
    with pytest.raises(DbKeyError):
        sqlcipher.sync_connect(path, keys[1:])

    dest = home.parent / "bk"
    r = CliRunner().invoke(app, ["db", "backup", str(dest)])
    assert r.exit_code == 0, r.output
    assert sqlcipher.is_encrypted_file(dest / "memory.db")
    assert MARK.encode() not in (dest / "memory.db").read_bytes()
    assert '"memory_db_sqlcipher": true' in (dest / "manifest.json").read_text()
    path.unlink()
    r = CliRunner().invoke(app, ["db", "restore", str(dest), "--yes"])
    assert r.exit_code == 0, r.output
    store = MemoryStore(path, dims=8)
    assert store.fts("fact", "quokka", 5) == [fid]
    store.close()


def test_doctor_row(home: Path) -> None:
    level, detail, fix = sqlcipher.memory_status(home, "sqlcipher")
    assert level == "ok" and "not created yet" in detail and "sqlite-vec on sqlcipher3: ok" in detail
    MemoryStore(home / "memory.db", dims=8, encryption="off").close()
    level, detail, fix = sqlcipher.memory_status(home, "sqlcipher")
    assert level == "error" and "auoreo db encrypt" in fix
    level, detail, fix = sqlcipher.memory_status(home, "columns")
    assert level == "ok" and "state.db only" in detail
