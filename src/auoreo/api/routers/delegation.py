"""Delegation control + spec route aliases (spec §16.1, §16.8): the `delegation.pause` switch,
a session's subagents, and `/api/mailbox` (alias of `/api/teams/{team_id}/mailbox`)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import MailMessage
from auoreo.schema.teams_h2 import DelegationPause, DelegationState, SubagentInfo
from auoreo.teams.mailbox import mail_dict
from auoreo.teams.service import TeamsService

router = APIRouter(tags=["teams"], dependencies=AUTH)


def _teams(svc: Services) -> TeamsService:
    teams = svc.ext.get("teams")
    if teams is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "teams extension is not running")
    return teams  # type: ignore[no-any-return]


def _state(teams: TeamsService) -> DelegationState:
    return DelegationState(
        paused=teams.control.paused,
        source="api" if teams.control.pause_override is not None else "settings",
        running=teams.fleet.total,
        queued=len(teams.fleet.waiters),
    )


@router.get("/api/delegation", response_model=DelegationState)
async def delegation_state(svc: ServicesDep) -> DelegationState:
    return _state(_teams(svc))


@router.put("/api/delegation/pause", response_model=DelegationState)
async def set_pause(svc: ServicesDep, body: DelegationPause) -> DelegationState:
    """Flip `delegation.pause` at runtime (audited); new spawns are refused while paused."""
    teams = _teams(svc)
    await teams.control.set_paused(body.paused, actor="api")
    return _state(teams)


@router.get("/api/sessions/{session_id}/subagents", response_model=list[SubagentInfo])
async def subagents(svc: ServicesDep, session_id: str) -> list[SubagentInfo]:
    if await svc.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown session '{session_id}'")
    rows = await _teams(svc).control.children(session_id)
    return [SubagentInfo.model_validate(r) for r in rows]


@router.get("/api/mailbox", response_model=list[MailMessage])
async def mailbox(svc: ServicesDep, team_id: str, member: str | None = None) -> list[MailMessage]:
    """Alias of `GET /api/teams/{team_id}/mailbox` (spec §16.8 endpoint name)."""
    teams = _teams(svc)
    if await teams.get_team(team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown team '{team_id}'")
    return [MailMessage.model_validate(mail_dict(r)) for r in await teams.mail.messages(team_id, member)]
