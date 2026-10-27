"""`POST /api/env/import-dotenv`: the one-time, user-confirmed import of existing `.env` files.

AUOREO never loads a `.env` at startup. This import is the only reader: it takes
`<home>/.env` and the current project's `<cwd>/.env` (if present), and moves secret-ish names
(catalog secrets, `*_API_KEY/_TOKEN/_SECRET/_PASSWORD/_KEY`) into secrets.db and
`AUOREO_<FIELD>` names into the state.db settings store (validated as one write). Protected
`[env-only]` names are never imported. A project `.env` is repository-controlled, so it is held
to `env_rules.project_allowed` (model/agent names and provider keys only). Provider keys also
create provider connections (validated by listing the provider's models). The files themselves
are not modified — the response tells the user to delete them.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from auoreo.core import env_store
from auoreo.core.env_catalog import FIELDS, SECRET_RE, WEB_MANAGED, by_field, secret_names
from auoreo.core.env_rules import REDIRECT, is_protected, project_allowed
from auoreo.core.settings_store import BOOTSTRAP
from auoreo.gateway import providers as reg
from auoreo.schema.env import DotenvImport, DotenvMove, DotenvProvider

WriteSettings = Callable[[Any, dict[str, Any], str], Awaitable[Any]]
KEY_PROVIDER: dict[str, str] = {
    s.secret: s.id for s in reg.PROVIDERS.values() if s.secret and s.id != "azure"
}
"""Imported secret name → provider connection it creates (Azure needs deployment names: manual)."""


def dotenv_files(svc: Any) -> list[Path]:
    """`<home>/.env` then `<cwd>/.env`, those that exist (deduplicated)."""
    cwd = Path(getattr(svc, "cwd", None) or Path.cwd())
    out: list[Path] = []
    for path in (Path(svc.settings.home) / ".env", cwd / ".env"):
        if path.is_file() and path.resolve() not in {p.resolve() for p in out}:
            out.append(path)
    return out


def _parse(field: str, raw: str) -> Any:
    entry = by_field().get(field)
    if entry is not None and entry.type == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def classify(name: str, *, project: bool = False) -> DotenvMove:
    """Where one `.env` name goes: `secrets`, `settings`, `skipped` (protected) or `kept`."""
    field = name.removeprefix("AUOREO_").lower() if name.startswith("AUOREO_") else None
    if is_protected(name):
        return DotenvMove(name=name, to="skipped", reason="env-only: set it in the real environment")
    if project and not project_allowed(name):
        return DotenvMove(
            name=name, to="skipped", reason="a project .env may not set this (repository-controlled)"
        )
    if field in BOOTSTRAP:
        return DotenvMove(
            name=name, to="skipped", reason="bootstrap setting: real environment or CLI flag only"
        )
    if field in WEB_MANAGED or field == "server_password":
        return DotenvMove(name=name, to="kept", reason="not managed from the web")
    if field is not None and field in FIELDS:
        return DotenvMove(name=name, to="settings")
    keyish = SECRET_RE.search(name) is not None and REDIRECT.search(name) is None
    if field is None and (name in secret_names() or keyish):
        return DotenvMove(name=name, to="secrets", provider=KEY_PROVIDER.get(name))
    return DotenvMove(name=name, to="kept", reason="not a setting or a key: not imported")


def plan(
    paths: list[Path], home: Path | None = None
) -> tuple[list[DotenvMove], dict[str, Any], dict[str, str]]:
    """`(moves, settings updates, secrets to store)`; a later file wins for the same name."""
    moves: dict[str, DotenvMove] = {}
    settings: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for path in paths:
        project = home is not None and path.parent.resolve() != Path(home).resolve()
        for name, value in dotenv_values(path).items():
            if not name or value is None or not value.strip():
                continue
            move = classify(name, project=project).model_copy(update={"file": str(path)})
            moves[name] = move
            if move.to == "settings":
                field = name.removeprefix("AUOREO_").lower()
                settings[field] = _parse(field, value)
            elif move.to == "secrets":
                secrets[name] = value
    return list(moves.values()), settings, secrets


async def _connect(svc: Any, secrets: dict[str, str]) -> list[DotenvProvider]:
    from auoreo.core import provider_store, providers_service

    have = {c.provider for c in await provider_store.connections(svc.db)}
    jobs: list[tuple[str, str | None, str | None]] = [
        (prov, value, None) for name, value in secrets.items() if (prov := KEY_PROVIDER.get(name))
    ]
    if base := secrets.get("OLLAMA_API_BASE"):
        jobs.append(("ollama", None, base))
    out: list[DotenvProvider] = []
    for prov, key, base_url in jobs:
        if prov in have:
            continue
        try:
            res = await providers_service.add(svc, prov, key, base_url, None, None, [])
        except providers_service.ProviderError as exc:
            out.append(DotenvProvider(provider=prov, ok=False, error=str(exc)))
            continue
        cid = res.connection.id if res.connection else None
        out.append(DotenvProvider(provider=prov, ok=res.ok, connection_id=cid, error=res.error))
        have.add(prov)
    return out


async def import_dotenv(svc: Any, *, dry_run: bool, write_settings: WriteSettings) -> DotenvImport:
    from auoreo.core.live_settings import live_of

    files = dotenv_files(svc)
    names = [str(p) for p in files]
    if not files:
        return DotenvImport(
            dry_run=dry_run, moved=[], counts={}, note="No .env file found: nothing to import"
        )
    moves, updates, secrets = plan(files, svc.settings.home)
    counts = dict(Counter(m.to for m in moves))
    if dry_run:
        return DotenvImport(
            dry_run=True, moved=moves, counts=counts, note="Preview: nothing was changed", files=names
        )
    if updates:
        data = env_store.read(svc.settings.home)
        data.update(updates)
        await write_settings(svc, data, "import")  # one validated write; 422 leaves everything
    for name, value in secrets.items():
        await asyncio.to_thread(svc.secrets.set, name, value)
    if secrets:
        await live_of(svc).secrets_changed(None, "import")
    providers = await _connect(svc, secrets)
    note = (
        f"Moved {len(secrets) + len(updates)} value(s). AUOREO never reads .env files: delete "
        + " and ".join(names)
        + "."
    )
    return DotenvImport(
        dry_run=False, moved=moves, counts=counts, note=note, files=names, providers=providers
    )


__all__ = ["KEY_PROVIDER", "classify", "dotenv_files", "import_dotenv", "plan"]
