"""Doctor rows for the web-managed environment: a leftover `.env` (ignored) and provider
connections (Settings → Models) whose last check failed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auoreo.core.diagnostics import Check


def dotenv_rows(st: Any, cwd: Path | None = None) -> list[Check]:
    files = [p for p in (Path(st.home) / ".env", (cwd or Path.cwd()) / ".env") if p.is_file()]
    if not files:
        return [Check("dotenv", "ok", "no .env file (settings live in state.db, keys in secrets.db)")]
    names = ", ".join(str(p) for p in dict.fromkeys(files))
    return [
        Check(
            "dotenv",
            "warn",
            f"{names} exists but is ignored: AUOREO never reads .env files",
            "Settings → Environment → “Import existing .env” moves its values into the database;"
            " then delete it",
        )
    ]


def provider_rows(st: Any) -> list[Check]:
    """Connections whose last model-list check failed (sync read of state.db, names only)."""
    from auoreo.core import settings_store

    path = settings_store.db_file(st.home)
    if not path.is_file():
        return []
    try:
        con = settings_store.connect(path, readonly=True)
        try:
            rows = con.execute("SELECT provider, status FROM provider_connections").fetchall()
        finally:
            con.close()
    except Exception:  # pre-v8 database / locked: the credentials rows still report
        return []
    if not rows:
        return [Check("providers", "ok", "no provider connected yet (Settings → Models)")]
    bad = [f"{p} ({s})" for p, s in rows if s in {"invalid", "error"}]
    if bad:
        hint = "Settings → Models: refresh, or replace the key"
        return [Check("providers", "warn", f"last check failed: {', '.join(bad)}", hint)]
    return [Check("providers", "ok", f"{len(rows)} provider connection(s) ok")]


async def env_checks(st: Any) -> list[Check]:
    return [*dotenv_rows(st), *provider_rows(st)]


__all__ = ["dotenv_rows", "env_checks", "provider_rows"]
