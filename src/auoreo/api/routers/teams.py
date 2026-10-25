"""Teams (spec §16.4/§16.8): teams with member liveness, and each team's mailbox."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import MailMessage, TeamDetail, TeamInfo, TeamMemberInfo
from auoreo.teams.mailbox import mail_dict
from auoreo.teams.service import TeamsService

router = APIRouter(prefix="/api/teams", tags=["teams"], dependencies=AUTH)


def _teams(svc: Services) -> TeamsService:
    teams = svc.ext.get("teams")
    if teams is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "teams extension is not running")
    return teams  # type: ignore[no-any-return]


@router.get("", response_model=list[TeamInfo])
async def list_teams(svc: ServicesDep) -> list[TeamInfo]:
    teams = _teams(svc)
    return [
        TeamInfo(
            id=t.id,
            name=t.name,
            description=t.description,
            lead_session_id=t.lead_session_id,
            created_at=t.created_at,
            members=len(await teams.members(t.id)),
        )
        for t in await teams.list_teams()
    ]


@router.get("/{team_id}", response_model=TeamDetail)
async def get_team(svc: ServicesDep, team_id: str) -> TeamDetail:
    teams = _teams(svc)
    team = await teams.get_team(team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown team '{team_id}'")
    peers = [TeamMemberInfo.model_validate(p) for p in await teams.peers(team_id)]
    return TeamDetail(
        id=team.id,
        name=team.name,
        description=team.description,
        lead_session_id=team.lead_session_id,
        created_at=team.created_at,
        members=len(peers) - 1,
        peers=peers,
    )


@router.get("/{team_id}/mailbox", response_model=list[MailMessage])
async def mailbox(svc: ServicesDep, team_id: str, member: str | None = None) -> list[MailMessage]:
    teams = _teams(svc)
    if await teams.get_team(team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown team '{team_id}'")
    return [MailMessage.model_validate(mail_dict(r)) for r in await teams.mail.messages(team_id, member)]
