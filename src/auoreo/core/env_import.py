"""`POST /api/env/import-dotenv`: move `<home>/.env` into the managed stores.

Only the user's own `<home>/.env` is read (never a project `.env`). Secret-ish names (catalog
secrets, `*_API_KEY/_TOKEN/_SECRET/_PASSWORD/_KEY`) go to secrets.db, `AUOREO_<FIELD>` names to
settings.json (validated as one write), protected `[env-only]` names are never imported and
anything else is left where it is. The file itself is not modified — the response recommends
clearing it. Names the import moved are dropped from `os.environ` when a `.env` file had put
them there, so the managed value takes effect immediately.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

from dotenv import dotenv_values

from auoreo.core import env_store
from auoreo.core.dotenv import _REDIRECT, from_dotenv, is_protected
from auoreo.core.env_catalog import FIELDS, SECRET_RE, WEB_MANAGED, by_field, secret_names
from auoreo.schema.env import DotenvImport, DotenvMove

WriteSettings = Callable[[Any, dict[str, Any], str], Awaitable[Any]]


def _parse(field: str, raw: str) -> Any:
    entry = by_field().get(field)
    if entry is not None and entry.type == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def classify(name: str) -> DotenvMove:
    """Where one `.env` name goes: `secrets`, `settings`, `skipped` (protected) or `kept`."""
    field = name.removeprefix("AUOREO_").lower() if name.startswith("AUOREO_") else None
    if is_protected(name):
        return DotenvMove(name=name, to="skipped", reason="env-only: set it in the real environment")
    if field in WEB_MANAGED or field == "server_password":
        return DotenvMove(name=name, to="kept", reason="not managed from the web")
    if field is not None and field in FIELDS:
        return DotenvMove(name=name, to="settings")
    keyish = SECRET_RE.search(name) is not None and _REDIRECT.search(name) is None
    if field is None and (name in secret_names() or keyish):
        return DotenvMove(name=name, to="secrets")
    return DotenvMove(name=name, to="kept", reason="not a setting or a key: left in .env")


def plan(path: Any) -> tuple[list[DotenvMove], dict[str, Any], dict[str, str]]:
    """`(moves, settings updates, secrets to store)` for the `.env` at `path`."""
    moves: list[DotenvMove] = []
    settings: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for name, value in dotenv_values(path).items():
        if not name or value is None or not value.strip():
            continue
        move = classify(name)
        moves.append(move)
        if move.to == "settings":
            field = name.removeprefix("AUOREO_").lower()
            settings[field] = _parse(field, value)
        elif move.to == "secrets":
            secrets[name] = value
    return moves, settings, secrets


async def import_dotenv(svc: Any, *, dry_run: bool, write_settings: WriteSettings) -> DotenvImport:
    from auoreo.core.live_settings import live_of

    path = svc.settings.home / ".env"
    if not path.is_file():
        return DotenvImport(dry_run=dry_run, moved=[], counts={}, note=f"{path} does not exist")
    moves, updates, secrets = plan(path)
    counts = dict(Counter(m.to for m in moves))
    if dry_run:
        note = "Preview: nothing was changed"
        return DotenvImport(dry_run=True, moved=moves, counts=counts, note=note)
    if updates:
        data = env_store.read(svc.settings.home)
        data.update(updates)
        await write_settings(svc, data, "import")  # one validated write; 422 leaves everything
    for name, value in secrets.items():
        await asyncio.to_thread(svc.secrets.set, name, value)
    for name in [*secrets, *(f"AUOREO_{f.upper()}" for f in updates)]:
        if from_dotenv(name):
            os.environ.pop(name, None)  # the managed value wins from now on
    if updates:
        await live_of(svc).reload("import")
    if secrets:
        await live_of(svc).secrets_changed(None, "import")
    note = f"Moved {len(secrets) + len(updates)} value(s). Clear {path} so they are not loaded twice."
    return DotenvImport(dry_run=False, moved=moves, counts=counts, note=note)


__all__ = ["import_dotenv", "plan"]
