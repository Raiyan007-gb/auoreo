"""`/api/sessions/{id}/plan` (current update_plan steps) and `/api/sessions/{id}/sources`
(per-session enable/disable of MCP servers and plugins, honoured by tool + skill selection)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.runtime.session_sources import current_plan, disabled_sources, set_disabled, tool_source
from auoreo.schema.insights import SessionPlan, SessionSource, SessionSources, SessionSourcesIn

router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=AUTH)


async def _require(svc: Services, session_id: str) -> None:
    if await svc.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


@router.get("/{session_id}/plan", response_model=SessionPlan)
async def get_plan(session_id: str, svc: ServicesDep) -> SessionPlan:
    await _require(svc, session_id)
    return SessionPlan.model_validate(await current_plan(svc, session_id))


def _catalog(svc: Services) -> list[dict[str, Any]]:
    """Every MCP server and plugin with its global status, tool count and skills."""
    tools: dict[str, int] = {}
    for t in svc.harness.tools.values():
        src = tool_source(t)
        tools[src] = tools.get(src, 0) + 1
    skills: dict[str, list[str]] = {}
    for s in svc.harness.all_skills.values():
        skills.setdefault(s.source, []).append(s.name)
    mcp_status = svc.mcp.status() if svc.mcp is not None else {}
    rows: list[dict[str, Any]] = []
    for name, d in sorted(svc.harness.snapshot.mcp.items()):
        state = mcp_status.get(name) or ("not connected" if d.manifest.enabled else "disabled")
        rows.append({"id": f"mcp:{name}", "kind": "mcp", "name": name, "status": state})
    host = svc.ext.get("plugins")
    plugins = {r["name"]: r["status"] for r in host.rows()} if host is not None else {}
    for name in sorted(set(svc.harness.plugins) | set(plugins)):
        rows.append(
            {"id": f"plugin:{name}", "kind": "plugin", "name": name, "status": plugins.get(name, "stopped")}
        )
    for r in rows:
        r["tools"] = tools.get(r["id"], 0)
        r["skills"] = sorted(skills.get(r["id"], []))
    return rows


async def _sources(svc: Services, session_id: str) -> SessionSources:
    off = await disabled_sources(svc, session_id)
    rows = [SessionSource(**r, enabled=r["id"] not in off) for r in _catalog(svc)]
    return SessionSources(session_id=session_id, sources=rows, disabled=off)


@router.get("/{session_id}/sources", response_model=SessionSources)
async def get_sources(session_id: str, svc: ServicesDep) -> SessionSources:
    await _require(svc, session_id)
    return await _sources(svc, session_id)


@router.put("/{session_id}/sources", response_model=SessionSources)
async def put_sources(session_id: str, body: SessionSourcesIn, svc: ServicesDep) -> SessionSources:
    """Replace the session's disabled list; applies from the session's next turn."""
    await _require(svc, session_id)
    try:
        await set_disabled(svc, session_id, body.disabled)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await _sources(svc, session_id)
