"""G5a: `auoreo db inspect` stats and the `auoreo debug bundle|snapshot` archives (spec §20.3).

Everything that leaves this module is redacted *unconditionally*: the pattern redactor runs
whatever `redact_secrets` says, and every known secret value (secret store entries, the
server password, `*_KEY|*TOKEN*|*SECRET*|*PASSWORD*` env vars) is replaced verbatim, so a
secret with no recognisable shape is still caught. Database stats are counts, never rows:
`events.json` holds per-type event counts and the seq range, never payloads (§24.9 L882).
PII is masked too (§25.10): the user's home directory becomes `~`, and the login name,
e-mail addresses and non-loopback IP addresses are replaced by `<user>`/`<email>`/`<ip>`.
The manifest carries the audit `chain_head{seq, hash}` (§25.10 L1021).
"""

from __future__ import annotations

import getpass
import ipaddress
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
from auoreo.core.redaction import REDACTION_VERSION, pattern_count, redact, sentinel
from auoreo.core.settings import Settings

SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "DSN")
MIN_SECRET_LEN = 6
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
        self.home = _home_prefix()
        self.user = _login_name()

    def text(self, s: str) -> str:
        for v in self.values:
            s = s.replace(v, sentinel("secret"))
        return pii(redact(s, force=True), self.home, self.user)

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [self.obj(v) for v in o]
        return o


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")
COMMON_USERS = frozenset({"root", "user", "admin", "runner", "ubuntu", "auoreo"})


def _home_prefix() -> str | None:
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError):
        return None
    return home if len(home) > 1 else None


def _login_name() -> str | None:
    try:
        name = getpass.getuser()
    except (OSError, KeyError):
        return None
    return name if len(name) >= 3 and name.lower() not in COMMON_USERS else None


def _ip(m: re.Match[str]) -> str:
    try:
        addr = ipaddress.ip_address(m.group(0))
    except ValueError:
        return m.group(0)
    return m.group(0) if addr.is_loopback or addr.is_unspecified else "<ip>"


def pii(s: str, home: str | None, user: str | None) -> str:
    """Home prefix → `~`; login name, e-mails, non-loopback IPs masked (idempotent)."""
    if home:
        s = s.replace(home, "~")
    s = EMAIL_RE.sub("<email>", s)
    if user:
        s = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(user)}(?![A-Za-z0-9_])", "<user>", s)
    s = IPV4_RE.sub(_ip, s)
    return IPV6_RE.sub(_ip, s)


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


def _read_only(st: Settings) -> sqlite3.Connection | None:
    from auoreo.core import sqlcipher

    if not st.db_path.is_file() or sqlcipher.is_encrypted_file(st.db_path):
        return None
    return sqlite3.connect(f"file:{st.db_path}?mode=ro", uri=True)


def event_stats(st: Settings) -> dict[str, Any]:
    """Counts, never rows (§24.9 L882): total, seq range and per-type counts of the event log."""
    out: dict[str, Any] = {"total": 0, "first_seq": None, "last_seq": None, "by_type": {}}
    con = _read_only(st)
    if con is None:
        return out
    try:
        total, first, last = con.execute("SELECT COUNT(*), MIN(seq), MAX(seq) FROM events").fetchone()
        by_type = con.execute("SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY type").fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    return {"total": total, "first_seq": first, "last_seq": last, "by_type": dict(by_type)}


def chain_head(st: Settings) -> dict[str, Any] | None:
    """The audit hash chain's head `{seq, hash}` (verify against `auoreo doctor`)."""
    con = _read_only(st)
    if con is None:
        return None
    try:
        row = con.execute("SELECT seq, row_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return {"seq": row[0], "hash": row[1]} if row else {"seq": 0, "hash": None}


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
        "events.json": event_stats(st),
    }
    logs = log_tails(st.home)
    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "chain_head": chain_head(st),
        "redaction": {
            "mode": "forced (patterns + known secret values + PII: home prefix, user, e-mail, IP)",
            "version": REDACTION_VERSION,
            "pattern_count": pattern_count(),
            "known_values": len(scrub.values),
        },
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
