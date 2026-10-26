"""`/api/memory/prefs/*` (G6): PREFS.md proposals inbox. Approving records the
`prefs.approved` event's `seq` in the proposal and in the applied PREFS.md entry."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.memory.prefs import PrefsError, Proposal, ProposalNotFound, inbox
from auoreo.schema.prefs import PrefsDecision, PrefsProposal, PrefsProposalIn

router = APIRouter(prefix="/api/memory/prefs", tags=["memory"], dependencies=AUTH)


def _out(p: Proposal) -> PrefsProposal:
    return PrefsProposal.model_validate(p.to_dict())


def _http(exc: PrefsError) -> HTTPException:
    if isinstance(exc, ProposalNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if "already" in str(exc):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/proposals", response_model=list[PrefsProposal])
async def list_proposals(
    svc: ServicesDep,
    status_: Annotated[Literal["pending", "approved", "denied"] | None, Query(alias="status")] = None,
) -> list[PrefsProposal]:
    """Newest first; filter with `?status=pending`."""
    return [_out(p) for p in inbox(svc.settings).proposals(status_)]


@router.post(
    "/proposals",
    response_model=PrefsProposal,
    status_code=status.HTTP_201_CREATED,
    dependencies=[IDEMPOTENT],
)
async def create_proposal(body: PrefsProposalIn, svc: ServicesDep) -> PrefsProposal:
    """A human can queue a proposal too (it still needs an approval to apply)."""
    try:
        prop = inbox(svc.settings).propose(
            body.action, body.content, old_text=body.old_text, reason=body.reason, actor="user"
        )
    except PrefsError as exc:
        raise _http(exc) from None
    await svc.emit(
        "server", "prefs.proposed", {"proposal_id": prop.id, "action": prop.action, "actor": "user"}
    )
    return _out(prop)


@router.post("/proposals/{proposal_id}/approve", response_model=PrefsProposal, dependencies=[IDEMPOTENT])
async def approve(proposal_id: str, svc: ServicesDep) -> PrefsProposal:
    box = inbox(svc.settings)
    try:
        prop = box.check_applicable(proposal_id)
        ev = await svc.events.append(
            prop.session_id or "server",
            "prefs.approved",
            {"proposal_id": prop.id, "action": prop.action, "by": "user"},
        )
        return _out(box.approve(proposal_id, approval_event_id=ev.seq, by="user"))
    except PrefsError as exc:
        raise _http(exc) from None


@router.post("/proposals/{proposal_id}/deny", response_model=PrefsProposal, dependencies=[IDEMPOTENT])
async def deny(proposal_id: str, body: PrefsDecision, svc: ServicesDep) -> PrefsProposal:
    try:
        prop = inbox(svc.settings).deny(proposal_id, reason=body.reason, by="user")
    except PrefsError as exc:
        raise _http(exc) from None
    await svc.emit(prop.session_id or "server", "prefs.denied", {"proposal_id": prop.id, "by": "user"})
    return _out(prop)
