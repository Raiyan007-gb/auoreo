"""The web/CLI-managed settings store: the `app_settings` table of `<home>/state.db` (sync).

Every process (server, `auoreo config`, a `Settings()` built anywhere) reads and writes it
through plain `sqlite3` (or SQLCipher with the same key ring `core.db` uses), so the pydantic
source in `core.settings` works before any async engine exists. One row per top-level settings
key; every write is one transaction that snapshots the previous content into
`app_settings_history` (newest `HISTORY_KEEP` kept, for undo) and bumps
`app_settings_meta.version`, which the live watcher polls instead of a file mtime.

Bootstrap fields (`BOOTSTRAP`) are needed before the database can be opened (or are bound
before it is), so they are env/CLI-only: never read from nor written to the store.

`migrate_legacy(home)` is the one-time import of a pre-v8 `~/.auoreo/settings.json`: when
the file exists and the store is empty its (validated) content is imported, the file is renamed
`settings.json.migrated-<timestamp>` and a pending audit note is left for the server to record.
Nothing else is ever read from settings.json.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
HISTORY_KEEP = 10
LEGACY_FILE = "settings.json"
BOOTSTRAP: dict[str, str] = {
    "home": "the runtime home locates state.db itself",
    "db_encryption": "decides how state.db is opened, so it cannot live inside it",
    "host": "the listening socket is bound before settings are read from the database",
    "port": "the listening socket is bound before settings are read from the database",
    "workers": "uvicorn forks its workers before the database is opened",
}
"""Env/CLI-only fields, with the reason the web shows (they stay in RESTART_REQUIRED too)."""
NEVER_STORED = frozenset({"server_password"})
_GUARD = threading.local()


class SettingsStoreError(ValueError):
    pass


def db_file(home: Path) -> Path:
    return Path(home) / "state.db"


def _encryption(encryption: str | None) -> str:
    return encryption or os.environ.get("AUOREO_DB_ENCRYPTION") or "off"


def _policy(policy: str | None) -> str:
    return policy or os.environ.get("AUOREO_KEYRING") or "allow-file"


def connect(path: Path, *, readonly: bool, encryption: str | None = None, policy: str | None = None) -> Any:
    """A DB-API connection to state.db (read-only URI when `readonly`)."""
    if _encryption(encryption) == "sqlcipher":
        from auoreo.core import sqlcipher
        from auoreo.core.crypto_columns import DbKeyring

        ring = DbKeyring(path.parent, _policy(policy))
        con = sqlcipher.sync_connect(path, ring.load() if readonly else ring.ensure())
    elif readonly:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    else:
        con = sqlite3.connect(path, timeout=5)
    con.execute("PRAGMA busy_timeout=5000")
    return con


@contextlib.contextmanager
def _open(path: Path, readonly: bool, encryption: str | None, policy: str | None) -> Iterator[Any]:
    con = connect(path, readonly=readonly, encryption=encryption, policy=policy)
    try:
        yield con
    finally:
        con.close()


def _load(raw: Any) -> Any:
    """A stored value: JSON text, or a number SQLite's NUMERIC affinity already converted."""
    return json.loads(raw) if isinstance(raw, str | bytes) else raw


def _missing_table(exc: Exception) -> bool:
    return "no such table" in str(exc)


def read(home: Path, *, encryption: str | None = None, policy: str | None = None) -> dict[str, Any]:
    """`{key: value}` of the store; `{}` when state.db or the table does not exist yet."""
    path = db_file(home)
    if not path.is_file():
        return {}
    try:
        with _open(path, True, encryption, policy) as con:
            rows = con.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
    except Exception as exc:
        if _missing_table(exc):
            return {}
        raise SettingsStoreError(f"state.db settings are unreadable: {exc}") from exc
    try:
        return {str(k): _load(v) for k, v in rows}
    except ValueError as exc:
        raise SettingsStoreError(f"state.db settings hold invalid JSON: {exc}") from exc


def version(home: Path, *, encryption: str | None = None, policy: str | None = None) -> int:
    """The change counter (0 = never written, -1 = no database yet)."""
    path = db_file(home)
    if not path.is_file():
        return -1
    try:
        with _open(path, True, encryption, policy) as con:
            row = con.execute("SELECT value FROM app_settings_meta WHERE key = 'version'").fetchone()
    except Exception as exc:
        if _missing_table(exc):
            return 0
        raise
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def ensure_schema(home: Path, *, encryption: str | None = None, policy: str | None = None) -> None:
    """Create/upgrade state.db (Alembic) when the v8 tables are missing."""
    path = db_file(home)
    if version(home, encryption=encryption, policy=policy) >= 0 and _has_table(path, encryption, policy):
        return
    from auoreo.core.db import SCHEMA_VERSION
    from auoreo.core.db_migrate import migrate

    path.parent.mkdir(parents=True, exist_ok=True)
    keyring = None
    if _encryption(encryption) == "sqlcipher":
        from auoreo.core.crypto_columns import DbKeyring

        keyring = DbKeyring(path.parent, _policy(policy))
    migrate(path, keyring=keyring, max_version=SCHEMA_VERSION)


def _has_table(path: Path, encryption: str | None, policy: str | None) -> bool:
    with _open(path, True, encryption, policy) as con:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'"
        ).fetchone()
    return row is not None


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    bad = sorted(k for k in data if k in BOOTSTRAP or k in NEVER_STORED)
    if bad:
        why = "; ".join(f"{k}: {BOOTSTRAP.get(k, 'a secret: use `auoreo secrets set`')}" for k in bad)
        raise SettingsStoreError(f"not stored in the database ({why}) — set it in the environment")
    return data


def write(
    home: Path,
    data: dict[str, Any],
    *,
    source: str = "api",
    snapshot: bool = True,
    encryption: str | None = None,
    policy: str | None = None,
) -> None:
    """Replace the store with `data` in one transaction (history snapshot + version bump).
    Validation is the caller's job (`core.env_store.validate`)."""
    _clean(data)
    if not getattr(_GUARD, "migrating", False):
        migrate_legacy(home, encryption=encryption, policy=policy)
    ensure_schema(home, encryption=encryption, policy=policy)
    with _open(db_file(home), False, encryption, policy) as con:
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        try:
            old = {str(k): v for k, v in con.execute("SELECT key, value FROM app_settings").fetchall()}
            now = _now()
            if snapshot:
                body = json.dumps({k: _load(v) for k, v in old.items()})
                con.execute(
                    "INSERT INTO app_settings_history (snapshot, source, created_at) VALUES (?, ?, ?)",
                    (body, source, now),
                )
                con.execute(
                    "DELETE FROM app_settings_history WHERE id NOT IN "
                    "(SELECT id FROM app_settings_history ORDER BY id DESC LIMIT ?)",
                    (HISTORY_KEEP,),
                )
            for key in set(old) - set(data):
                con.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            for key, value in data.items():
                body = json.dumps(value, ensure_ascii=False)
                if key in old and _load(old[key]) == value:
                    continue
                con.execute(
                    "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                    (key, body, now, source),
                )
            _bump(con)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def _bump(con: Any) -> None:
    con.execute(
        "INSERT INTO app_settings_meta (key, value) VALUES ('version', '1') ON CONFLICT(key) "
        "DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
    )


def set_meta(home: Path, key: str, value: str | None, **kw: Any) -> None:
    ensure_schema(home, **kw)
    with _open(db_file(home), False, kw.get("encryption"), kw.get("policy")) as con:
        if value is None:
            con.execute("DELETE FROM app_settings_meta WHERE key = ?", (key,))
        else:
            con.execute(
                "INSERT INTO app_settings_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        con.commit()


def get_meta(home: Path, key: str, **kw: Any) -> str | None:
    path = db_file(home)
    if not path.is_file():
        return None
    try:
        with _open(path, True, kw.get("encryption"), kw.get("policy")) as con:
            row = con.execute("SELECT value FROM app_settings_meta WHERE key = ?", (key,)).fetchone()
    except Exception as exc:
        if _missing_table(exc):
            return None
        raise
    return str(row[0]) if row else None


def history_count(home: Path, **kw: Any) -> int:
    path = db_file(home)
    if not path.is_file():
        return 0
    try:
        with _open(path, True, kw.get("encryption"), kw.get("policy")) as con:
            return int(con.execute("SELECT count(*) FROM app_settings_history").fetchone()[0])
    except Exception as exc:
        if _missing_table(exc):
            return 0
        raise


def pop_history(home: Path, **kw: Any) -> dict[str, Any]:
    """Remove and return the newest snapshot (undo consumes it: twice goes two steps back)."""
    path = db_file(home)
    if history_count(home, **kw) == 0:
        raise SettingsStoreError("nothing to undo")
    with _open(path, False, kw.get("encryption"), kw.get("policy")) as con:
        row = con.execute("SELECT id, snapshot FROM app_settings_history ORDER BY id DESC LIMIT 1").fetchone()
        con.execute("DELETE FROM app_settings_history WHERE id = ?", (row[0],))
        con.commit()
    data = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    if not isinstance(data, dict):
        raise SettingsStoreError("the history entry is not a JSON object")
    return data


# ------------------------------------------------------------------ legacy settings.json
def _validated(home: Path, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop keys that fail validation (reported) so one bad value does not lose the rest."""
    from pydantic import ValidationError

    from auoreo.core.settings import Settings

    dropped: list[str] = []
    for _ in range(len(data) + 1):
        try:
            Settings(home=home, **data)
            return data, dropped
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]} & set(data)
            if not bad:
                break
            dropped += sorted(bad)
            data = {k: v for k, v in data.items() if k not in bad}
    return {}, dropped + sorted(data)


def migrate_legacy(home: Path, **kw: Any) -> dict[str, Any] | None:
    """One-time import of `<home>/settings.json` into an empty store; returns what happened."""
    legacy = Path(home) / LEGACY_FILE
    if getattr(_GUARD, "migrating", False) or not legacy.is_file():
        return None
    _GUARD.migrating = True
    try:
        if read(home, **kw):
            return None  # the database already owns the settings: the file is left alone
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cannot migrate %s (%s): fix or remove it", legacy, exc)
            return None
        if not isinstance(raw, dict):
            log.warning("cannot migrate %s: not a JSON object", legacy)
            return None
        skipped = sorted(k for k in raw if k in BOOTSTRAP or k in NEVER_STORED)
        data, dropped = _validated(home, {k: v for k, v in raw.items() if k not in skipped})
        write(home, data, source="migration", snapshot=False, **kw)
        target = legacy.with_name(f"{LEGACY_FILE}.migrated-{time.strftime('%Y%m%d-%H%M%S')}")
        legacy.replace(target)
        info = {"imported": sorted(data), "skipped": skipped, "invalid": dropped, "file": target.name}
        set_meta(home, "pending_audit", json.dumps(info), **kw)
        log.warning(
            "migrated %s into state.db (%d keys; env-only skipped: %s; invalid dropped: %s); "
            "the file was renamed to %s and is no longer read",
            legacy,
            len(data),
            ", ".join(skipped) or "none",
            ", ".join(dropped) or "none",
            target.name,
        )
        return info
    finally:
        _GUARD.migrating = False


def migrating() -> bool:
    return bool(getattr(_GUARD, "migrating", False))


__all__ = [
    "BOOTSTRAP",
    "HISTORY_KEEP",
    "NEVER_STORED",
    "SettingsStoreError",
    "connect",
    "db_file",
    "ensure_schema",
    "get_meta",
    "history_count",
    "migrate_legacy",
    "migrating",
    "pop_history",
    "read",
    "set_meta",
    "version",
    "write",
]
