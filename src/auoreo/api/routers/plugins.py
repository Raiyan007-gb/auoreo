"""`/api/plugins`: list, start/stop/reload, plugin skills, connector pairing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import PluginInfo, PluginPair, PluginPaired, PluginSkillInfo

router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=AUTH)


def _host(svc: Services) -> Any:
    host = svc.ext.get("plugins")
    if host is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "plugin host is not running")
    return host


def _info(host: Any, name: str) -> PluginInfo:
    for row in host.rows():
        if row["name"] == name:
            return PluginInfo(**row)
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown plugin '{name}'")


@router.get("", response_model=list[PluginInfo])
async def list_plugins(svc: ServicesDep) -> list[PluginInfo]:
    return [PluginInfo(**r) for r in _host(svc).rows()]


async def _op(svc: Services, name: str, op: str) -> PluginInfo:
    host = _host(svc)
    try:
        await getattr(host, f"{op}_plugin")(name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc).strip("'\"")) from exc
    return _info(host, name)


@router.post("/{name}/start", response_model=PluginInfo)
async def start_plugin(name: str, svc: ServicesDep) -> PluginInfo:
    return await _op(svc, name, "start")


@router.post("/{name}/stop", response_model=PluginInfo)
async def stop_plugin(name: str, svc: ServicesDep) -> PluginInfo:
    return await _op(svc, name, "stop")


@router.post("/{name}/reload", response_model=PluginInfo)
async def reload_plugin(name: str, svc: ServicesDep) -> PluginInfo:
    """Re-read definitions from disk, then restart this plugin (reinstalls on a hash change)."""
    return await _op(svc, name, "reload")


@router.get("/{name}/skills", response_model=list[PluginSkillInfo])
async def plugin_skills(name: str, svc: ServicesDep) -> list[PluginSkillInfo]:
    try:
        skills = _host(svc).skills(name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown plugin '{name}'") from exc
    return [PluginSkillInfo(name=s.name, description=s.description, body=s.body) for s in skills]


@router.post("/{name}/pair", response_model=PluginPaired)
async def pair(name: str, body: PluginPair, svc: ServicesDep) -> PluginPaired:
    """Allowlist the chat that was shown this pairing code (connector plugins, e.g. telegram)."""
    try:
        chat_id = await _host(svc).pair(name, body.code)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown plugin '{name}'") from exc
    if chat_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown or expired pairing code")
    return PluginPaired(paired=True, chat_id=chat_id)
