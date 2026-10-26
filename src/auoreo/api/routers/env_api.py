"""`/api/env` — Settings → Environment: every catalog variable, live-applied writes, undo and
`.env` import (custom variables: `env_custom_api`).

- `GET /api/env` — sections with effective values (non-secrets, redacted), `is_set` for secrets,
  source / tier / type / choices / default / live / writable.
- `PUT /api/env/settings/{name}` — validated settings.json write, applied live (`settings.changed`).
- `PUT|DELETE /api/env/secrets/{name}`, `POST …/rotate` — write-only secrets.db (`secret.changed`).
- `POST /api/env/undo` — restore the previous settings.json; `POST /api/env/import-dotenv` — move
  `~/.auoreo/.env` keys into secrets.db / settings.json (protected names are never imported).

Auth: normal auth + cookie CSRF, capability (per-session) tokens refused, bodies ≤ 64 KiB.
An env-sourced value answers 409, an `[env-only]` knob 403. No response carries a secret value.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from auoreo.api.deps import ServicesDep, require_auth
from auoreo.core import env_store
from auoreo.core.env_catalog import by_name, secret_names
from auoreo.core.env_import import import_dotenv
from auoreo.core.env_view import listing, lock_of
from auoreo.core.live_settings import AppliedChange, live_of
from auoreo.core.secrets import SecretStoreError
from auoreo.core.services import Services
from auoreo.schema.env import (
    DotenvImport,
    DotenvImportRequest,
    EnvApplied,
    EnvListing,
    EnvSecretRotate,
    EnvSecretWrite,
    EnvSettingWrite,
)

MAX_BODY = 64 * 1024


def no_capability(request: Request) -> None:
    """Per-session capability tokens (and session-scoped JWTs) never manage the environment."""
    who = (request.scope.get("state") or {}).get("auoreo_principal")
    claims = getattr(who, "claims", None)
    if getattr(who, "kind", None) == "cap" or (claims is not None and claims.session_id is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "capability tokens cannot manage the environment")


def bounded(request: Request) -> None:
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"body larger than {MAX_BODY} bytes")


ENV_AUTH = [Depends(require_auth), Depends(no_capability), Depends(bounded)]
router = APIRouter(prefix="/api/env", tags=["env"], dependencies=ENV_AUTH)


def note_for(res: AppliedChange) -> str:
    if res.restart_required:
        return "Saved — restart required for: " + ", ".join(res.restart_required)
    if res.live:
        return "Applied — the agent uses it from its next step"
    return "Saved — nothing changed"


def applied(name: str | None, action: str, res: AppliedChange) -> EnvApplied:
    return EnvApplied(
        name=name,
        action=action,
        changed=res.changed,
        live=res.live,
        restart_required=res.restart_required,
        note=note_for(res),
    )


async def audit(svc: Services, action: str, detail: dict[str, Any]) -> None:
    await svc.audit.record(actor="api", action=action, decision="allow", detail=detail)


async def store_state(svc: Services) -> tuple[set[str], dict[str, int]]:
    try:
        names = await asyncio.to_thread(svc.secrets.names)
        updated = await asyncio.to_thread(svc.secrets.updated_at)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return set(names), updated


@router.get("", response_model=EnvListing)
async def get_env(svc: ServicesDep) -> EnvListing:
    stored, updated = await store_state(svc)
    return EnvListing.model_validate(listing(svc, live_of(svc), stored, updated))


def _entry(name: str, kinds: tuple[str, ...]) -> Any:
    e = by_name().get(name)
    if e is None or e.kind not in kinds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown variable '{name}'")
    return e


def _check_lock(e: Any, source: str) -> None:
    if (lock := lock_of(e, source)) is not None:
        raise HTTPException(lock.status, f"{e.name}: {lock.reason}")


async def write_settings(svc: Services, data: dict[str, Any], source: str = "api") -> AppliedChange:
    """Validate + write settings.json (history kept), then apply live."""
    try:
        await asyncio.to_thread(env_store.write, svc.settings.home, data)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errs[:2000]) from exc
    except env_store.EnvStoreError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await live_of(svc).reload(source)


@router.put("/settings/{name}", response_model=EnvApplied)
async def put_setting(name: str, body: EnvSettingWrite, svc: ServicesDep) -> EnvApplied:
    """Set (or with `value: null` reset) one setting in settings.json and apply it live."""
    e = _entry(name, ("setting",))
    live = live_of(svc)
    try:
        data = env_store.read(svc.settings.home)
    except env_store.EnvStoreError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _check_lock(e, env_store.setting_source(e.name, e.field, data, live.overrides))
    res = await write_settings(svc, env_store.set_path(data, e.field, body.value))
    await audit(svc, "settings.write", {"keys": [e.field]})
    return applied(e.name, "reset" if body.value is None else "set", res)


async def _secret_op(svc: Services, name: str, action: str, op: Any) -> EnvApplied:
    e = _entry(name, ("secret",))
    _check_lock(e, env_store.env_source(name) or "secrets")  # server_password: 403 (field-backed)
    if name not in secret_names():
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{name} is not stored in secrets.db")
    try:
        await asyncio.to_thread(op)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await audit(svc, "secret.write", {"name": name, "action": action})
    await live_of(svc).secrets_changed(name, action)
    return EnvApplied(
        name=name, action=action, live=[name], note="Applied — the agent uses it from its next step"
    )


@router.put("/secrets/{name}", response_model=EnvApplied)
async def put_secret(name: str, body: EnvSecretWrite, svc: ServicesDep) -> EnvApplied:
    """Store a catalog secret (write-only; the response never echoes it)."""
    return await _secret_op(svc, name, "set", lambda: svc.secrets.set(name, body.value))


@router.post("/secrets/{name}/rotate", response_model=EnvApplied)
async def rotate_secret(name: str, body: EnvSecretRotate, svc: ServicesDep) -> EnvApplied:
    """New value; the previous one keeps resolving as `<name>#prev` for `grace_s`."""
    grace = svc.settings.h1b.rotate_grace_s if body.grace_s is None else body.grace_s
    now = time.time()
    return await _secret_op(svc, name, "rotate", lambda: svc.secrets.rotate(name, body.value, grace, now))


@router.delete("/secrets/{name}", response_model=EnvApplied)
async def delete_secret(name: str, svc: ServicesDep) -> EnvApplied:
    return await _secret_op(svc, name, "delete", lambda: svc.secrets.delete(name))


@router.post("/undo", response_model=EnvApplied)
async def undo(svc: ServicesDep) -> EnvApplied:
    """Restore the previous settings.json version (10 are kept) and apply it live."""
    try:
        await asyncio.to_thread(env_store.undo, svc.settings.home)
    except (env_store.EnvStoreError, ValidationError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)[:2000]) from exc
    res = await live_of(svc).reload("undo")
    await audit(svc, "settings.undo", {"keys": res.changed})
    return applied(None, "undo", res)


@router.post("/import-dotenv", response_model=DotenvImport)
async def post_import(body: DotenvImportRequest, svc: ServicesDep) -> DotenvImport:
    """Move `~/.auoreo/.env` into secrets.db / settings.json (`dry_run` previews)."""
    try:
        out = await import_dotenv(svc, dry_run=body.dry_run, write_settings=write_settings)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not body.dry_run:
        await audit(svc, "env.import", {"counts": out.counts})
    return out


__all__ = ["ENV_AUTH", "audit", "router", "write_settings"]
