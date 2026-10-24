from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.db import SessionRow
from auoreo.schema.chat import MessageOut, SessionCreate, SessionInfo

router = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=AUTH)


def _info(row: SessionRow) -> SessionInfo:
    return SessionInfo(
        id=row.id,
        title=row.title,
        agent=row.agent,
        workflow=row.workflow,
        model=row.model,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, svc: ServicesDep) -> SessionInfo:
    try:
        row = await svc.create_session(
            title=body.title, agent=body.agent, workflow=body.workflow, model=body.model
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc).strip("'\"")) from exc
    return _info(row)


@router.get("", response_model=list[SessionInfo])
async def list_sessions(svc: ServicesDep, limit: int = 100) -> list[SessionInfo]:
    return [_info(r) for r in await svc.list_sessions(min(max(limit, 1), 1000))]


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, svc: ServicesDep) -> SessionInfo:
    row = await svc.get_session(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return _info(row)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(session_id: str, svc: ServicesDep) -> list[MessageOut]:
    if await svc.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    history = await svc.coordinator.runner.load_history(session_id)
    return [
        MessageOut(
            idx=i,
            role=m["role"],
            content=m.get("content"),
            tool_calls=m.get("tool_calls"),
            tool_call_id=m.get("tool_call_id"),
            name=m.get("name"),
        )
        for i, m in enumerate(history)
    ]


@router.post("/{session_id}/cancel")
async def cancel(session_id: str, svc: ServicesDep) -> dict[str, bool]:
    """Advisory and idempotent: cancelling an idle or unknown session is not an error."""
    return {"cancelled": await svc.coordinator.cancel(session_id)}
