"""Kanban-lite boards (spec §16.6/§16.8): list and create tasks."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi import status as http

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import KanbanCreate, KanbanTask
from auoreo.teams.kanban import KanbanStore, task_dict

router = APIRouter(prefix="/api/kanban", tags=["kanban"], dependencies=AUTH)


def _store(svc: Services) -> KanbanStore:
    teams = svc.ext.get("teams")
    if teams is None:
        raise HTTPException(http.HTTP_503_SERVICE_UNAVAILABLE, "teams extension is not running")
    return teams.kanban  # type: ignore[no-any-return]


@router.get("", response_model=list[KanbanTask])
async def list_tasks(
    svc: ServicesDep,
    board: str | None = None,
    status: Literal["ready", "claimed", "blocked", "done"] | None = None,
) -> list[KanbanTask]:
    rows = await _store(svc).list_tasks(board, status)
    return [KanbanTask.model_validate(task_dict(r)) for r in rows]


@router.post("", response_model=KanbanTask, status_code=201)
async def create_task(svc: ServicesDep, body: KanbanCreate) -> KanbanTask:
    row = await _store(svc).create(body.board, body.title, body.body)
    return KanbanTask.model_validate(task_dict(row))
