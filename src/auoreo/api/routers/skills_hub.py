"""`/api/skills/*` (G6): skill sources (taps + MCP servers) and hub-managed skills. Installing
stays a CLI action (`auoreo skills install`); approving a quarantined skill works here too."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.schema.skills_hub import HubInstalledSkill, McpSkillSource, SkillSources
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
