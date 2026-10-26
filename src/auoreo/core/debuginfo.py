"""G5a: `auoreo db inspect` stats and the `auoreo debug bundle|snapshot` archives (spec §20.3).

Everything that leaves this module is redacted *unconditionally*: the pattern redactor runs
whatever `redact_secrets` says, and every known secret value (secret store entries, the
server password, `*_KEY|*TOKEN*|*SECRET*|*PASSWORD*` env vars) is replaced verbatim, so a
secret with no recognisable shape is still caught. Database stats are counts, never rows.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auoreo import __version__
from auoreo.core.redaction import redact, sentinel
from auoreo.core.settings import Settings

SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "DSN")
MIN_SECRET_LEN = 6
RECENT_EVENTS = 200
LOG_TAIL_LINES = 400
PACKAGES = (
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "litellm",
    "pydantic",
    "mcp",
    "typer",
    "websockets",
)
DB_FILES = ("state.db", "state.db-wal", "state.db-shm", "memory.db", "secrets.db")


# ------------------------------------------------------------------ forced redaction
class Scrubber:
    """Pattern redaction + verbatim replacement of every secret value we can enumerate."""

    def __init__(self, st: Settings) -> None:
        values: set[str] = set()
        if st.server_password:
            values.add(st.server_password)
        for name, value in os.environ.items():
            if any(w in name.upper() for w in SECRET_ENV_WORDS) and len(value) >= MIN_SECRET_LEN:
                values.add(value)
        try:
            from auoreo.core.secrets import SecretStore

            store = SecretStore(st.home, st.keyring)
            for name in store.names():
                if (v := store.get(name)) and len(v) >= MIN_SECRET_LEN:
                    values.add(v)
        except Exception:  # an unreadable store has no values to leak
            pass
        self.values = sorted(values, key=len, reverse=True)

    def text(self, s: str) -> str:
        for v in self.values:
            s = s.replace(v, sentinel("secret"))
        return redact(s)

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [self.obj(v) for v in o]
        return o


# ------------------------------------------------------------------ db inspect
def _alembic_head() -> str | None:
    try:
        from alembic.script import ScriptDirectory

        from auoreo.core.db_migrate import alembic_config

        head = ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()
        return str(head) if head else None
    except Exception:
        return None


def db_inspect(st: Settings) -> dict[str, Any]:
    """Read-only: schema version, Alembic head/current, row counts per table, file sizes."""
    from auoreo.core import sqlcipher
    from auoreo.core.db import SCHEMA_VERSION

    home = st.db_path.parent
    sizes = {name: (home / name).stat().st_size for name in DB_FILES if (home / name).is_file()}
    out: dict[str, Any] = {
        "path": str(st.db_path),
        "exists": st.db_path.is_file(),
        "code_schema_version": SCHEMA_VERSION,
        "alembic_head": _alembic_head(),
        "files": sizes,
        "schema_version": None,
        "alembic_current": None,
        "tables": {},
    }
    if not st.db_path.is_file():
        return out
    if sqlcipher.is_encrypted_file(st.db_path):
        out["encrypted"] = True
        out["note"] = "SQLCipher file: table counts need the key (run inside the server: GET /api/doctor)"
        return out
    con = sqlite3.connect(f"file:{st.db_path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out["tables"] = {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
        if "schema_meta" in names:
            row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            out["schema_version"] = int(row[0]) if row else None
        if "alembic_version" in names:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            out["alembic_current"] = row[0] if row else None
    finally:
        con.close()
    return out


# ------------------------------------------------------------------ versions + logs
def versions() -> dict[str, Any]:
    from importlib import metadata

    from auoreo.core.buildinfo import code_sha

    pkgs: dict[str, str | None] = {}
    for name in PACKAGES:
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    return {
        "auoreo": __version__,
        "code_sha": code_sha(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pkgs,
    }


def recent_events(st: Settings, limit: int = RECENT_EVENTS) -> list[dict[str, Any]]:
    """The newest durable events (the event log is AUOREO's log of record)."""
    from auoreo.core import sqlcipher

    if not st.db_path.is_file() or sqlcipher.is_encrypted_file(st.db_path):
        return []
    con = sqlite3.connect(f"file:{st.db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT seq, aggregate_id, type, data, created_at FROM events ORDER BY seq DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for seq, agg, kind, data, created in reversed(rows):
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
        except ValueError:
            parsed = str(data)[:2000]
        out.append(
            {"seq": seq, "aggregate_id": agg, "type": kind, "data": parsed, "created_at": str(created)}
        )
    return out


def log_tails(home: Path, lines: int = LOG_TAIL_LINES) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(home.glob("*.log")):
        try:
            out[path.name] = "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            )
        except OSError:
            continue
    return out


SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api_?key|private_?key|access_?key|credential|dsn)"
)
REF_PREFIXES = ("secret:", "env:", "keyring:")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")  # a value naming an env var / secret is not a secret


def mask_secret_keys(o: Any) -> Any:
    """Mask string values under secret-looking keys (`secret:<name>` references are kept)."""
    if isinstance(o, dict):
        return {
            k: (
                sentinel(str(k))
                if isinstance(v, str)
                and v
                and SECRET_KEY_RE.search(str(k))
                and not v.startswith(REF_PREFIXES)
                and not ENV_NAME_RE.match(v)
                else mask_secret_keys(v)
            )
            for k, v in o.items()
        }
    if isinstance(o, list):
        return [mask_secret_keys(v) for v in o]
    return o


def settings_view(st: Settings) -> dict[str, Any]:
    data = mask_secret_keys(st.model_dump(mode="json"))
    data["server_password"] = "set" if st.server_password else None
    path = st.home / "settings.json"
    raw: Any = None
    if path.is_file():
        try:
            raw = mask_secret_keys(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raw = f"unreadable: {exc}"
    return {"effective": data, "settings_json": raw}


# ------------------------------------------------------------------ bundle
async def build_bundle(st: Settings, out: Path) -> list[str]:
    """Write the debug zip; returns the member names. Nothing is uploaded."""
    from auoreo.core import diagnostics

    scrub = Scrubber(st)
    doctor = [c.as_dict() for c in await diagnostics.run_checks(st)]
    members: dict[str, Any] = {
        "versions.json": versions(),
        "settings.json": settings_view(st),
        "doctor.json": doctor,
        "db.json": db_inspect(st),
        "events.json": recent_events(st),
    }
    logs = log_tails(st.home)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "redaction": "forced (patterns + known secret values)",
        "members": [*members, *(f"logs/{n}" for n in logs)],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, value in members.items():
            zf.writestr(name, json.dumps(scrub.obj(value), indent=2, default=str))
        for name, text in logs.items():
            zf.writestr(f"logs/{name}", scrub.text(text))
    return ["manifest.json", *manifest["members"]]
