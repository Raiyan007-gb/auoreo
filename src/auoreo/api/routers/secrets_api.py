"""I2: `/api/secrets` — names-only listing, write-through and rotation (spec §25.14 L1067).

`PUT /api/secrets/{name}` stores a value, `POST /api/secrets/{name}/rotate` rotates it with a
grace window (`<name>#prev` keeps resolving). Both write an audit row (`secrets.write` /
`secrets.rotate`, names only) and hot-reload the gateway's credentials and Router, so a new
provider key works without a restart. Responses never echo a value.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.live_settings import live_of
from auoreo.core.secrets import ROTATION_SUFFIXES, UPDATED_SUFFIX, SecretStoreError
from auoreo.core.services import Services
from auoreo.schema.secrets_api import SecretNames, SecretRotate, SecretWrite, SecretWriteResult

router = APIRouter(prefix="/api/secrets", tags=["secrets"], dependencies=AUTH)
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,127}$")
_META = ("#rotated_at", "#prev_expires", UPDATED_SUFFIX)


def _check(name: str) -> str:
    if not _NAME.match(name) or name.startswith("AUOREO_") or ".." in name or name == "secrets-key":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid secret name")
    return name


def _reload(svc: Services) -> bool:
    reload = getattr(svc.gateway, "reload_credentials", None)
    if reload is None:
        return False
    reload()
    return True


async def _audit(svc: Services, action: str, detail: dict[str, object]) -> None:
    await svc.audit.record(actor="api", action=action, decision="allow", detail=detail)


@router.get("", response_model=SecretNames)
async def list_secrets(svc: ServicesDep) -> SecretNames:
    try:
        names = await asyncio.to_thread(svc.secrets.names)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return SecretNames(names=[n for n in names if not n.endswith(_META)])


@router.put("/{name:path}", response_model=SecretWriteResult)
async def put_secret(name: str, body: SecretWrite, svc: ServicesDep) -> SecretWriteResult:
    """Store `value` under `name` (overwrites; use `/rotate` to keep a grace window)."""
    _check(name)
    if name.endswith((*ROTATION_SUFFIXES, UPDATED_SUFFIX)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "rotation metadata is managed by /rotate")
    try:
        await asyncio.to_thread(svc.secrets.set, name, body.value)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await _audit(svc, "secrets.write", {"name": name})
    reloaded = _reload(svc)
    await live_of(svc).note_secret_write(name, "set")  # --- ENV: secret.changed (name only)
    return SecretWriteResult(name=name, action="set", reloaded=reloaded)


@router.post("/{name:path}/rotate", response_model=SecretWriteResult)
async def rotate_secret(name: str, body: SecretRotate, svc: ServicesDep) -> SecretWriteResult:
    """New value into `name`; the old one resolves as `<name>#prev` for `grace_s` seconds and
    the gateway tries it once (audited `secret.rotated_fallback`) if the new key is rejected."""
    _check(name)
    grace = svc.settings.h1b.rotate_grace_s if body.grace_s is None else body.grace_s
    now = time.time()
    try:
        kept = await asyncio.to_thread(svc.secrets.rotate, name, body.value, grace, now)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await _audit(svc, "secrets.rotate", {"name": name, "grace_s": grace, "kept_previous": kept})
    expires = datetime.fromtimestamp(int(now) + grace, UTC) if kept else None
    reloaded = _reload(svc)
    await live_of(svc).note_secret_write(name, "rotate")  # --- ENV
    return SecretWriteResult(
        name=name, action="rotate", kept_previous=kept, prev_expires_at=expires, reloaded=reloaded
    )


__all__ = ["router"]
