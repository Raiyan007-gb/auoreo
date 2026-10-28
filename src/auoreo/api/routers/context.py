"""`GET /api/sessions/{id}/context` (G5b): context occupancy for the web `ctx_viz` gauge, and
`GET /api/context?session_id=…` for many sessions in one call (the Usage table).

Counts what the next request would carry, with the same helpers the runner and the
compaction meter use (`build_spec`, `tool_schema`, `gateway.count_tokens`)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.gateway.resolve import resolve_model
from auoreo.gateway.types import tool_schema
from auoreo.harness.orchestrator import build_spec
from auoreo.schema.ui import SessionContext

one_router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=AUTH)
batch_router = APIRouter(prefix="/api/context", tags=["sessions"], dependencies=AUTH)
"""Own prefix: a path under /api/sessions/ would be taken by the `/{session_id}` routes."""
BATCH_MAX = 100


def _count(svc: Services, model: str, payload: list[Any]) -> int:
    if not payload:
        return 0
    return int(svc.gateway.count_tokens(model, json.dumps(payload, default=str)))


@one_router.get("/{session_id}/context", response_model=SessionContext)
async def session_context(session_id: str, svc: ServicesDep) -> SessionContext:
    """System / tools / messages token breakdown against the model's context window."""
    row = await svc.get_session(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return await _context(svc, row)


@batch_router.get("", response_model=list[SessionContext])
async def sessions_context(
    svc: ServicesDep, session_id: Annotated[list[str], Query(max_length=BATCH_MAX)]
) -> list[SessionContext]:
    """The same breakdown for up to 100 sessions at once; unknown ids are left out."""
    out = []
    for sid in dict.fromkeys(session_id):
        row = await svc.get_session(sid)
        if row is not None:
            out.append(await _context(svc, row))
    return out


async def _context(svc: Services, row: Any) -> SessionContext:
    session_id = row.id
    agent_name = row.agent if row.agent in svc.harness.agents else svc.settings.agent
    manifest = svc.harness.agent(agent_name).manifest
    model = resolve_model(svc.settings, session_override=row.model, agent_model=manifest.model)
    mode = manifest.permission_mode or svc.settings.permission_mode
    spec = build_spec(svc.harness, agent_name, model, mode)
    history = await svc.coordinator.runner.load_history(session_id)
    system_msg: list[Any] = [{"role": "system", "content": spec.system_prompt}]
    tools = [tool_schema(t.name, t.manifest.description, t.manifest.parameters) for t in spec.tools]
    pressure = _count(svc, model, [*system_msg, *history])
    tools_tokens = _count(svc, model, tools)
    th = svc.coordinator.runner.compaction.thresholds(model)
    window = max(1, int(th.window))
    return SessionContext(
        session_id=session_id,
        model=model,
        context_window=window,
        system_tokens=_count(svc, model, system_msg),
        tools_tokens=tools_tokens,
        messages_tokens=_count(svc, model, history),
        pressure=pressure,
        auto_compact_at=int(th.auto),
        ratio=round((pressure + tools_tokens) / window, 4),
    )


router = APIRouter()
"""What `api/app.py` includes: both routes (sub-routers are copied in at include time, so last)."""
router.include_router(one_router)
router.include_router(batch_router)
