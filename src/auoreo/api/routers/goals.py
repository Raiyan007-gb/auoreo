"""Long-running goals (spec §2.3): list, create, update, absorb."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi import status as http

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.db import GoalRow
from auoreo.goals.service import GoalError, goal_data, goals_of
from auoreo.schema.goals import GoalAbsorb, GoalCreate, GoalInfo, GoalPatch

router = APIRouter(prefix="/api/goals", tags=["goals"], dependencies=AUTH)


def _info(row: GoalRow) -> GoalInfo:
    return GoalInfo.model_validate(goal_data(row))


def _refused(exc: GoalError) -> HTTPException:
    code = http.HTTP_404_NOT_FOUND if "not found" in str(exc) else http.HTTP_409_CONFLICT
    return HTTPException(code, str(exc))


@router.get("", response_model=list[GoalInfo])
async def list_goals(
    svc: ServicesDep,
    status: Literal["active", "paused", "done", "dropped", "absorbed", "all"] | None = None,
    limit: int = 100,
) -> list[GoalInfo]:
    """No `status` lists every goal; otherwise only that status."""
    return [_info(r) for r in await goals_of(svc).list(status, limit)]


@router.post("", response_model=GoalInfo, status_code=201)
async def create_goal(svc: ServicesDep, body: GoalCreate) -> GoalInfo:
    try:
        return _info(await goals_of(svc).create(body.title, body.description))
    except GoalError as exc:
        raise _refused(exc) from exc


@router.get("/{goal_id}", response_model=GoalInfo)
async def get_goal(goal_id: int, svc: ServicesDep) -> GoalInfo:
    row = await goals_of(svc).get(goal_id)
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "goal not found")
    return _info(row)


@router.patch("/{goal_id}", response_model=GoalInfo)
async def update_goal(goal_id: int, svc: ServicesDep, body: GoalPatch) -> GoalInfo:
    try:
        row = await goals_of(svc).update(
            goal_id,
            status=body.status,
            progress=body.progress,
            title=body.title,
            description=body.description,
            evidence_session=body.evidence_session_id,
        )
    except GoalError as exc:
        raise _refused(exc) from exc
    return _info(row)


@router.post("/{goal_id}/absorb", response_model=GoalInfo)
async def absorb_goal(goal_id: int, svc: ServicesDep, body: GoalAbsorb) -> GoalInfo:
    """Absorb `goal_id` into `body.into`; returns the absorbing goal with merged evidence."""
    try:
        return _info(await goals_of(svc).absorb(goal_id, body.into))
    except GoalError as exc:
        raise _refused(exc) from exc
