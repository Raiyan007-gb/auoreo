"""SQLCipher whole-file encryption of `state.db` (`db_encryption = "sqlcipher"`, spec L1069).

Async path: SQLAlchemy's aiosqlite dialect with `async_creator` handing it an
`aiosqlite.Connection` whose connector is `sqlcipher3.connect` + `PRAGMA key` (aiosqlite only
needs a callable returning a DB-API connection). A dialect subclass swaps the exception
classes to `sqlcipher3.dbapi2`'s so `IntegrityError` & co. are still translated into
`sqlalchemy.exc`. Alembic uses a sync pysqlcipher engine with a `creator`.

The raw 32-byte primary `auoreo-db-key` is used as the SQLCipher key (`x'<hex>'`, no KDF);
opening tries every key in the ring so a rotation interrupted before `PRAGMA rekey` still opens.
F6: `memory.db` (a separate sync sqlite3 + sqlite-vec store) is opened through `open_memory`,
which keys it with the same ring in sqlcipher mode; sqlite-vec and FTS5 load on a sqlcipher3
connection exactly as on stdlib sqlite3.
"""

from __future__ import annotations

import base64
import importlib
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy.dialects import registry
from sqlalchemy.dialects.sqlite.aiosqlite import AsyncAdapt_aiosqlite_dbapi, SQLiteDialect_aiosqlite
from sqlalchemy.dialects.sqlite.pysqlcipher import SQLiteDialect_pysqlcipher
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite

from auoreo.core.crypto_columns import DbKeyError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

    from auoreo.core.crypto_columns import DbKeyring

PLAIN_HEADER = b"SQLite format 3\x00"
_ERRORS = ("DatabaseError", "Error", "IntegrityError", "NotSupportedError", "OperationalError")


def _dbapi() -> Any:
    return importlib.import_module("sqlcipher3.dbapi2")


def available() -> bool:
    try:
        _dbapi()
    except ImportError:
        return False
    return True


def check() -> tuple[bool, str]:
    """Importable + `PRAGMA cipher_version` on a throwaway keyed database."""
    try:
        mod = _dbapi()
    except ImportError as exc:
        return False, f"sqlcipher3 not installed ({exc})"
    with tempfile.TemporaryDirectory() as d:
        con = mod.connect(str(Path(d) / "probe.db"))
        try:
            con.execute("PRAGMA key = 'probe'")
            row = con.execute("PRAGMA cipher_version").fetchone()
            con.execute("CREATE TABLE t(x)")
        finally:
            con.close()
    if not row or not row[0]:
        return False, "sqlcipher3 imported but PRAGMA cipher_version is empty (plain SQLite build)"
    return True, f"SQLCipher {row[0]}"


def raw_key(key: bytes) -> str:
    return "x'" + base64.urlsafe_b64decode(key).hex() + "'"


def _keyed(path: Path | str, key: bytes) -> Any:
    con = _dbapi().connect(str(path), check_same_thread=False)
    con.execute(f'PRAGMA key = "{raw_key(key)}"')
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception:
        con.close()
        raise
    return con


def sync_connect(path: Path | str, keys: list[bytes]) -> Any:
    name = Path(path).name
    if not keys:
        raise DbKeyError("database key unavailable")
    last: Exception | None = None
    for key in keys:
        try:
            return _keyed(path, key)
        except Exception as exc:  # wrong key: "file is not a database"
            last = exc
    raise DbKeyError(f"{name} cannot be opened with any key in the ring ({last})")


def is_encrypted_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open("rb") as fh:
        return fh.read(16) != PLAIN_HEADER


# ------------------------------------------------------------------ SQLAlchemy dialects
class _CipherDBAPI(AsyncAdapt_aiosqlite_dbapi):
    def _init_dbapi_attributes(self) -> None:
        super()._init_dbapi_attributes()
        for name in (*_ERRORS, "ProgrammingError", "sqlite_version", "sqlite_version_info"):
            setattr(self, name, getattr(self.sqlite, name))


class AioSQLCipherDialect(SQLiteDialect_aiosqlite):
    driver = "auoreo_aiosqlcipher"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls) -> AsyncAdapt_aiosqlite_dbapi:
        return _CipherDBAPI(importlib.import_module("aiosqlite"), _dbapi())


class SyncSQLCipherDialect(SQLiteDialect_pysqlcipher):
    """pysqlcipher without its own `PRAGMA key` (the `creator` already keyed the connection)."""

    driver = "auoreo_sqlcipher"
    supports_statement_cache = True

    def on_connect_url(self, url: Any) -> Any:
        return SQLiteDialect_pysqlite.on_connect_url(self, url)


registry.register("sqlite.auoreo_aiosqlcipher", __name__, "AioSQLCipherDialect")
registry.register("sqlite.auoreo_sqlcipher", __name__, "SyncSQLCipherDialect")


def async_engine(path: Path, keyring: DbKeyring) -> AsyncEngine:
    import aiosqlite
    from sqlalchemy.ext.asyncio import create_async_engine

    async def creator() -> Any:
        keys = keyring.ensure()
        conn = aiosqlite.Connection(lambda: sync_connect(path, keys), 64)
        thread = getattr(conn, "_thread", None)
        if thread is not None:
            thread.daemon = True
        return await conn

    return create_async_engine(f"sqlite+auoreo_aiosqlcipher:///{path}", async_creator=creator)


def sync_engine(path: Path, keyring: DbKeyring) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(
        f"sqlite+auoreo_sqlcipher:///{path}", creator=lambda: sync_connect(path, keyring.ensure())
    )


# ------------------------------------------------------------------ conversion
def encrypt_file(path: Path, keyring: DbKeyring) -> Path:
    """Convert a plaintext `state.db` / `memory.db` in place via `sqlcipher_export` (stop the server first).
    A plaintext copy is kept as `state.db.plain-<ts>.bak`; returns that path."""
    if is_encrypted_file(path):
        raise DbKeyError(f"{path} is already encrypted")
    key = keyring.ensure()[0]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.plain-{stamp}.bak")
    src = sqlite3.connect(path)
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
    finally:
        src.close()
    tmp = path.with_name(path.name + ".enc.tmp")
    tmp.unlink(missing_ok=True)
    con = _dbapi().connect(str(path))
    try:
        _try_load_vec(con)  # memory.db carries vec0 tables; harmless elsewhere
        con.execute('ATTACH DATABASE ? AS enc KEY "' + raw_key(key) + '"', (str(tmp),))
        con.execute("SELECT sqlcipher_export('enc')")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        con.execute(f"PRAGMA enc.user_version = {int(version)}")
        con.execute("DETACH DATABASE enc")
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    shutil.move(tmp, path)
    sync_connect(path, [key]).close()  # proves the new file opens with the key
    return backup


def rekey(path: Path, old_keys: list[bytes], new_key: bytes) -> None:
    con = sync_connect(path, old_keys)
    try:
        con.execute(f'PRAGMA rekey = "{raw_key(new_key)}"')
    finally:
        con.close()


# ------------------------------------------------------------------ F6: memory.db
ENCRYPT_HINT = "stop the server and run `auoreo db encrypt --yes` (converts state.db and memory.db)"


def _try_load_vec(con: Any) -> bool:
    try:
        import sqlite_vec

        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        return True
    except Exception:
        return False


def resolve_mode(home: Path, mode: str | None = None) -> tuple[str, DbKeyring]:
    """`db_encryption` + key ring from `Settings()` (env + settings.json), as `core.db` does."""
    from auoreo.core.crypto_columns import DbKeyring

    try:
        from auoreo.core.settings import Settings

        st = Settings()
        return mode or st.db_encryption, DbKeyring(home, st.keyring)
    except Exception:  # unreadable settings never block opening the store
        return mode or "off", DbKeyring(home, "allow-file")


def open_memory(path: Path | str, mode: str | None = None) -> tuple[sqlite3.Connection, bool]:
    """Open `memory.db`: SQLCipher-keyed in sqlcipher mode (a plaintext file is refused),
    stdlib sqlite3 otherwise (an encrypted file is refused with a pointer to the setting).
    Returns `(connection, encrypted)`."""
    p = Path(path)
    if str(path) == ":memory:":
        return sqlite3.connect(":memory:", check_same_thread=False), False
    mode, keyring = resolve_mode(p.parent, mode)
    if mode != "sqlcipher":
        if is_encrypted_file(p):
            raise DbKeyError(f"{p} is SQLCipher-encrypted; set db_encryption=sqlcipher to open it")
        return sqlite3.connect(str(p), check_same_thread=False), False
    if not available():
        raise DbKeyError("db_encryption=sqlcipher needs the `sqlcipher` extra (sqlcipher3) for memory.db")
    if p.is_file() and p.stat().st_size > 0 and not is_encrypted_file(p):
        raise DbKeyError(f"{p} is plaintext but db_encryption=sqlcipher; {ENCRYPT_HINT}")
    return cast(sqlite3.Connection, sync_connect(p, keyring.ensure())), True  # DB-API twin


def memory_status(home: Path, mode: str) -> tuple[Literal["ok", "warn", "error"], str, str]:
    """Doctor row for memory.db: (level, detail, remedy)."""
    path = home / "memory.db"
    enc = is_encrypted_file(path)
    state = "not created yet" if not path.is_file() else ("SQLCipher-encrypted" if enc else "plaintext")
    if mode == "sqlcipher":
        if path.is_file() and not enc:
            return "error", f"{state} under db_encryption=sqlcipher (memory refuses to open)", ENCRYPT_HINT
        if not available():
            return "error", f"{state}; sqlcipher3 missing", "install the `sqlcipher` extra"
        vec = _vec_on_cipher()
        detail = f"{state}; sqlite-vec on sqlcipher3: {'ok' if vec else 'UNAVAILABLE (FTS-only search)'}"
        return (
            ("ok" if vec else "warn"),
            detail,
            "" if vec else "use a sqlcipher3 build with loadable extensions",
        )
    if enc:
        return "error", f"{state} but db_encryption={mode}", "set db_encryption=sqlcipher"
    note = " (columns mode covers state.db only)" if mode == "columns" else ""
    return "ok", f"{state}{note}", "set db_encryption=sqlcipher to encrypt memory.db" if note else ""


def _vec_on_cipher() -> bool:
    con = _dbapi().connect(":memory:")
    try:
        con.execute("PRAGMA key = 'probe'")
        return _try_load_vec(con) and bool(con.execute("SELECT vec_version()").fetchone())
    except Exception:
        return False
    finally:
        con.close()
