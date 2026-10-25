"""Proactive suggestions (consent-first): list, propose, accept, deny."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi import status as http

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.db import SuggestionRow
from auoreo.schema.goals import SuggestionAccepted, SuggestionCreate, SuggestionInfo, SuggestionProposed
from auoreo.suggestions import SuggestionError, suggestions_of

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"], dependencies=AUTH)


def _info(row: SuggestionRow) -> SuggestionInfo:
    return SuggestionInfo.model_validate(row, from_attributes=True)


def _refused(exc: SuggestionError) -> HTTPException:
    code = http.HTTP_404_NOT_FOUND if "not found" in str(exc) else http.HTTP_409_CONFLICT
    return HTTPException(code, str(exc))


@router.get("", response_model=list[SuggestionInfo])
async def list_suggestions(
    svc: ServicesDep,
    status: Literal["pending", "accepted", "denied", "expired", "tombstoned", "all"] | None = None,
    limit: int = 100,
) -> list[SuggestionInfo]:
    return [_info(r) for r in await suggestions_of(svc).list(status, limit)]


@router.post("", response_model=SuggestionProposed, status_code=201)
async def propose_suggestion(svc: ServicesDep, body: SuggestionCreate) -> SuggestionProposed:
    """Record a pending suggestion. Nothing runs until it is accepted."""
    try:
        row, created = await suggestions_of(svc).propose(
            body.kind,
            body.title,
            body.body,
            deliver=body.deliver,
            origin_session=body.origin_session,
            action=body.action,
            dedupe_key=body.dedupe_key,
            ttl_s=body.ttl_s,
        )
    except SuggestionError as exc:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return SuggestionProposed(suggestion=_info(row), created=created)


@router.post("/{suggestion_id}/accept", response_model=SuggestionAccepted)
async def accept_suggestion(suggestion_id: int, svc: ServicesDep) -> SuggestionAccepted:
    """Explicit consent; a prompt action then runs one turn (origin session or a new one)."""
    try:
        res = await suggestions_of(svc).accept(suggestion_id)
    except SuggestionError as exc:
        raise _refused(exc) from exc
    return SuggestionAccepted(suggestion=_info(res.row), session_id=res.session_id, run_id=res.run_id)


@router.post("/{suggestion_id}/deny", response_model=SuggestionInfo)
async def deny_suggestion(suggestion_id: int, svc: ServicesDep) -> SuggestionInfo:
    try:
        return _info(await suggestions_of(svc).deny(suggestion_id))
    except SuggestionError as exc:
        raise _refused(exc) from exc
