"""Teams (spec §16.4/§16.8): teams with member liveness, each team's mailbox, its blackboard
and its supervisor decisions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.schema.chat import MailMessage, TeamDetail, TeamInfo, TeamMemberInfo
from auoreo.schema.teams_board import BoardArtifact, BoardEntry, BoardPut, TeamDecision
from auoreo.teams.board import MAX_VALUE_BYTES, artifact_dict, check_key
from auoreo.teams.mailbox import TeamsError, mail_dict
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


# --- W1-B blackboard + supervisor decisions
async def _team_or_404(svc: Services, team_id: str) -> TeamsService:
    teams = _teams(svc)
    if await teams.get_team(team_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown team '{team_id}'")
    return teams


def _key(key: str) -> str:
    try:
        return check_key(key)
    except TeamsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/{team_id}/board", response_model=list[BoardEntry])
async def board_list(svc: ServicesDep, team_id: str, prefix: str = "") -> list[BoardEntry]:
    teams = await _team_or_404(svc, team_id)
    rows = await teams.board.list(team_id, prefix)
    return [BoardEntry.model_validate(artifact_dict(r, with_value=False)) for r in rows]


@router.get("/{team_id}/decisions", response_model=list[TeamDecision])
async def decisions(svc: ServicesDep, team_id: str, limit: int = 200) -> list[TeamDecision]:
    teams = await _team_or_404(svc, team_id)
    rows = await teams.supervisor.decisions(team_id, max(1, min(limit, 1000)))
    return [TeamDecision.model_validate(r) for r in rows]


@router.get("/{team_id}/board/{key:path}", response_model=BoardArtifact)
async def board_get(svc: ServicesDep, team_id: str, key: str) -> BoardArtifact:
    teams = await _team_or_404(svc, team_id)
    row = await teams.board.get(team_id, _key(key))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no board key '{key}' in team '{team_id}'")
    return BoardArtifact.model_validate(artifact_dict(row))


@router.put("/{team_id}/board/{key:path}", response_model=BoardArtifact)
async def board_put(svc: ServicesDep, team_id: str, key: str, body: BoardPut) -> BoardArtifact:
    """Write a key as the operator (`producer="api"`); audited as `board.put`."""
    teams = await _team_or_404(svc, team_id)
    _key(key)
    size = len(body.value.encode("utf-8"))
    detail: dict[str, object] = {"team": team_id, "key": key, "bytes": size}
    if size > MAX_VALUE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"board values are limited to {MAX_VALUE_BYTES} bytes"
        )
    try:
        row = await teams.board.put(team_id, key, body.value, "api")
    except TeamsError as exc:
        await svc.audit.record(
            actor="api", action="board.put", decision="deny", reason=str(exc), detail=detail
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await svc.audit.record(
        actor="api", action="board.put", decision="allow", detail={**detail, "version": row.version}
    )
    return BoardArtifact.model_validate(artifact_dict(row))
