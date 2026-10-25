from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import sessions_ops as ops
from auoreo.core.db import SessionRow
from auoreo.schema.chat import MessageOut, SessionCreate, SessionDeleted, SessionDetail, SessionInfo

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


def _detail(row: SessionRow) -> SessionDetail:
    return SessionDetail(**_info(row).model_dump(), parent_id=row.parent_id, archived=bool(row.archived))


async def _guard[T](op: Awaitable[T]) -> T:
    """Map session-op errors: unknown → 404, running → 409."""
    try:
        return await op
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found") from exc
    except ops.SessionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


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
async def list_sessions(svc: ServicesDep, limit: int = 100, archived: bool = False) -> list[SessionInfo]:
    """Archived sessions are hidden unless `?archived=true`."""
    rows = await svc.list_sessions(min(max(limit, 1), 1000), include_archived=archived)
    return [_info(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, svc: ServicesDep) -> SessionDetail:
    row = await svc.get_session(session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return _detail(row)


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


@router.post("/{session_id}/fork", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
async def fork(session_id: str, svc: ServicesDep) -> SessionDetail:
    """New session (same agent/workflow/model, `parent_id` set) with every committed message."""
    return _detail(await _guard(ops.fork_session(svc, session_id)))


@router.post("/{session_id}/archive", response_model=SessionDetail)
async def archive(session_id: str, svc: ServicesDep) -> SessionDetail:
    return _detail(await _guard(ops.set_archived(svc, session_id, True)))


@router.post("/{session_id}/unarchive", response_model=SessionDetail)
async def unarchive(session_id: str, svc: ServicesDep) -> SessionDetail:
    return _detail(await _guard(ops.set_archived(svc, session_id, False)))


@router.delete("/{session_id}", response_model=SessionDeleted)
async def delete(session_id: str, svc: ServicesDep) -> SessionDeleted:
    """Deletes the session and its messages (409 while running). Events stay."""
    await _guard(ops.delete_session(svc, session_id))
    return SessionDeleted(id=session_id, deleted=True)


@router.get(
    "/{session_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/json": {}, "text/markdown": {}}}},
)
async def export(session_id: str, svc: ServicesDep, format: Literal["json", "md"] = "json") -> Response:
    """`json` = session info + raw messages; `md` = readable transcript."""
    body = await _guard(ops.export_session(svc, session_id, format))
    media = "application/json" if format == "json" else "text/markdown; charset=utf-8"
    disposition = f'attachment; filename="{session_id}.{format}"'
    return Response(body, media_type=media, headers={"Content-Disposition": disposition})
