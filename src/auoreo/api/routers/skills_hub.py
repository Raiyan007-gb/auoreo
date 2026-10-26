"""`/api/skills/*` (G6, I5): skill sources (taps + MCP servers) and hub-managed skills —
search every tap, install (lands QUARANTINED, invisible to agents), approve, remove."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.schema.skills_hub import (
    HubInstalledSkill,
    HubInstallRequest,
    HubRemoved,
    HubSearchHit,
    HubSearchResult,
    McpSkillSource,
    SkillSources,
)
from auoreo.skillhub import HubError, hub_for, mcp_skills

router = APIRouter(prefix="/api/skills", tags=["skills"], dependencies=AUTH)


def _installed(svc: Any) -> list[HubInstalledSkill]:
    hub = hub_for(svc.settings)
    state = hub.verify()
    return [
        HubInstalledSkill.model_validate({**rec, "name": name, "integrity": state.get(name, "missing")})
        for name, rec in sorted(hub.lock().items())
    ]


@router.get("/sources", response_model=SkillSources)
async def sources(svc: ServicesDep) -> SkillSources:
    """Configured taps, skills offered by connected MCP servers, and hub-managed installs."""
    try:
        installed = _installed(svc)
    except HubError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from None
    return SkillSources(
        taps=list(svc.settings.g6.skills.taps),
        mcp=[McpSkillSource.model_validate(s) for s in await mcp_skills(svc)],
        installed=installed,
    )


@router.get("/hub/search", response_model=HubSearchResult)
async def search(svc: ServicesDep, q: str = "") -> HubSearchResult:
    """Search every configured tap (name or description substring; empty = everything).
    A tap that fails is listed in `errors`; the others still answer."""
    hub = hub_for(svc.settings)
    try:
        hits, errors = await hub.search(q[:200])
        lock = hub.lock()
    except HubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    return HubSearchResult(
        query=q,
        taps=list(hub.cfg.taps),
        hits=[
            HubSearchHit(
                name=h.name,
                description=h.description,
                tap=h.tap,
                version=h.version,
                installed=lock.get(h.name, {}).get("status"),
            )
            for h in hits
        ],
        errors=errors,
    )


@router.post(
    "/hub/install",
    response_model=HubInstalledSkill,
    status_code=status.HTTP_201_CREATED,
    dependencies=[IDEMPOTENT],
    responses={409: {"description": "Not in any tap, already installed, or refused"}},
)
async def install(body: HubInstallRequest, svc: ServicesDep) -> HubInstalledSkill:
    """Download a skill into quarantine (`~/.auoreo/skills/.quarantine/<name>/`, never
    discovered) and pin its sha256 in skills.lock. It stays disabled until approved."""
    hub = hub_for(svc.settings)
    if body.tap is not None and body.tap not in hub.cfg.taps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tap is not configured (g6.skills.taps)")
    try:
        rec = await hub.install(body.name, tap=body.tap, force=body.force)
    except HubError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await svc.emit(
        "server",
        "skills.installed",
        {"name": body.name, "sha256": rec["sha256"], "tap": rec["tap"], "status": "quarantined"},
    )
    return HubInstalledSkill.model_validate({**rec, "integrity": "ok"})


@router.post("/hub/{name}/approve", response_model=HubInstalledSkill, dependencies=[IDEMPOTENT])
async def approve(name: str, svc: ServicesDep) -> HubInstalledSkill:
    """Enable a quarantined skill (sha256 re-checked against skills.lock), then reload."""
    try:
        rec = hub_for(svc.settings).approve(name)
    except HubError as exc:
        code = status.HTTP_404_NOT_FOUND if "not quarantined" in str(exc) else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(exc)) from None
    svc.harness.reload()
    await svc.emit("server", "skills.approved", {"name": name, "sha256": rec["sha256"]})
    return HubInstalledSkill.model_validate({**rec, "integrity": "ok"})


@router.delete("/hub/{name}", response_model=HubRemoved)
async def remove(name: str, svc: ServicesDep) -> HubRemoved:
    """Delete a hub-managed skill (quarantined or approved) and its lock entry, then reload.
    Skills not installed by the hub are never touched (404)."""
    try:
        rec = hub_for(svc.settings).remove(name)
    except HubError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    if rec.get("status") == "approved":
        svc.harness.reload()
    await svc.emit("server", "skills.removed", {"name": name, "sha256": rec.get("sha256")})
    return HubRemoved(name=name, removed=True, status=rec.get("status", "quarantined"))
