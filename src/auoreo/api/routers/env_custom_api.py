"""`/api/env/custom` — user-defined variables (Settings → Environment → Custom variables).

A secret variable's value goes write-only to secrets.db; settings.json `env.custom.<NAME>` keeps
only its flags and `updated_at`. A plain variable lives entirely in settings.json. Names are
`^[A-Z][A-Z0-9_]{1,63}$` and may not shadow AUOREO_*, catalog, protected or loader/redirect
names. Every change applies live (next shell call / next turn) — `settings.changed`, plus
`secret.changed` for secret values.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import ServicesDep
from auoreo.api.routers.env_api import ENV_AUTH, applied, audit, write_settings
from auoreo.core import env_store
from auoreo.core.env_custom import name_problem
from auoreo.core.live_settings import AppliedChange, live_of
from auoreo.core.secrets import SecretStoreError
from auoreo.core.services import Services
from auoreo.schema.env import CustomVarWrite, EnvApplied

router = APIRouter(prefix="/api/env/custom", tags=["env"], dependencies=ENV_AUTH)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _custom(data: dict[str, Any]) -> dict[str, Any]:
    env = data.setdefault("env", {})
    if not isinstance(env, dict):
        env = data["env"] = {}
    custom = env.setdefault("custom", {})
    if not isinstance(custom, dict):
        custom = env["custom"] = {}
    return custom


def _read(svc: Services) -> dict[str, Any]:
    if env_store.env_source("AUOREO_ENV") is not None or "env" in live_of(svc).overrides:
        raise HTTPException(status.HTTP_409_CONFLICT, "AUOREO_ENV is set outside settings.json")
    try:
        return env_store.read(svc.settings.home)
    except env_store.EnvStoreError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


async def _store(svc: Services, op: Any) -> None:
    try:
        await asyncio.to_thread(op)
    except SecretStoreError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _meta(body: CustomVarWrite, value: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "secret": body.secret,
        "expose_to_shell": body.expose_to_shell,
        "expose_to_agent": body.expose_to_agent,
        "updated_at": _now(),
    }
    if not body.secret:
        meta["value"] = value or ""
    return meta


async def _save(svc: Services, name: str, body: CustomVarWrite, old: dict[str, Any] | None) -> AppliedChange:
    data = _read(svc)
    custom = _custom(data)
    was_secret = bool(old and old.get("secret"))
    value = body.value
    if body.secret:
        if value is None and not was_secret:
            if old is None or not old.get("value"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a secret variable needs a value")
            value = str(old["value"])  # plain → secret: the value moves into secrets.db
        if value is not None:
            await _store(svc, lambda: svc.secrets.set(name, value))
    else:
        if value is None:
            if was_secret:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "give a value to turn a secret variable into a plain one (the secret is never shown)",
                )
            value = str((old or {}).get("value") or "")
        if was_secret:
            await _store(svc, lambda: svc.secrets.delete(name))
    custom[name] = _meta(body, value)
    res = await write_settings(svc, data)
    if body.secret or was_secret:
        await live_of(svc).secrets_changed(name, "set" if body.secret else "delete")
    await audit(svc, "settings.write", {"keys": [f"env.custom.{name}"], "secret": body.secret})
    return res


@router.post("", response_model=EnvApplied, status_code=status.HTTP_201_CREATED)
async def create_custom(body: CustomVarWrite, svc: ServicesDep) -> EnvApplied:
    if (why := name_problem(body.name)) is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{body.name}: {why}")
    if body.name in _custom(_read(svc)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.name} already exists: edit it instead")
    if body.value is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a value is required")
    return applied(body.name, "create", await _save(svc, body.name, body, None))


@router.put("/{name}", response_model=EnvApplied)
async def update_custom(name: str, body: CustomVarWrite, svc: ServicesDep) -> EnvApplied:
    """Change flags and/or the value (`value` omitted keeps it). `body.name` must equal `name`."""
    if body.name != name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "renaming is not supported")
    old = _custom(_read(svc)).get(name)
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown custom variable '{name}'")
    return applied(name, "update", await _save(svc, name, body, old))


@router.delete("/{name}", response_model=EnvApplied)
async def delete_custom(name: str, svc: ServicesDep) -> EnvApplied:
    data = _read(svc)
    custom = _custom(data)
    old = custom.pop(name, None)
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown custom variable '{name}'")
    if old.get("secret"):
        await _store(svc, lambda: svc.secrets.delete(name))
    res = await write_settings(svc, data)
    if old.get("secret"):
        await live_of(svc).secrets_changed(name, "delete")
    await audit(svc, "settings.write", {"keys": [f"env.custom.{name}"], "action": "delete"})
    return applied(name, "delete", res)


__all__ = ["router"]
