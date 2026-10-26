"""G5a session endpoints: rename, share links (+ the public read-only view), retry, revert."""

from fastapi import APIRouter, HTTPException, Request, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.api.routers.chat import queue_full
from auoreo.api.routers.sessions import _detail, _guard
from auoreo.core import sessions_more as more
from auoreo.runtime.inbox import InboxFull
from auoreo.schema.chat import SessionDetail
from auoreo.schema.session_more import (
    RetryAccepted,
    RevertRequest,
    RevertResult,
    SessionRename,
    ShareCreated,
    SharedSession,
    SharesRevoked,
)

router = APIRouter()
authed = APIRouter(prefix="/api/sessions", tags=["sessions"], dependencies=AUTH)


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@authed.patch("/{session_id}", response_model=SessionDetail)
async def rename(session_id: str, body: SessionRename, svc: ServicesDep) -> SessionDetail:
    try:
        return _detail(await _guard(more.rename_session(svc, session_id, body.title)))
    except ValueError as exc:
        raise _bad_request(exc) from exc


@authed.post("/{session_id}/share", response_model=ShareCreated, status_code=status.HTTP_201_CREATED)
async def share(session_id: str, svc: ServicesDep, request: Request) -> ShareCreated:
    """Mint a read-only share link. The token is shown once; revoke with `DELETE …/share`."""
    token = await _guard(more.create_share(svc, session_id))
    path = f"/api/shared/{token}"
    base = (svc.settings.cli.share_base_url or str(request.base_url)).rstrip("/")
    return ShareCreated(session_id=session_id, token=token, path=path, url=base + path)


@authed.delete("/{session_id}/share", response_model=SharesRevoked)
async def unshare(session_id: str, svc: ServicesDep) -> SharesRevoked:
    return SharesRevoked(session_id=session_id, revoked=await _guard(more.revoke_shares(svc, session_id)))


@authed.post(
    "/{session_id}/retry",
    dependencies=[IDEMPOTENT],
    response_model=RetryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "Session is running"}, 429: {"description": "Queue full"}},
)
async def retry(session_id: str, svc: ServicesDep) -> RetryAccepted:
    """Cut the last user turn (recorded as `session.reverted`) and queue its text again."""
    try:
        run_id, text = await _guard(more.retry_session(svc, session_id))
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except InboxFull as exc:
        raise queue_full(exc) from exc
    return RetryAccepted(session_id=session_id, run_id=run_id, text=text)


@authed.post("/{session_id}/revert", response_model=RevertResult)
async def revert(session_id: str, body: RevertRequest, svc: ServicesDep) -> RevertResult:
    """Keep messages `0..to_idx`; the cut messages travel in a `session.reverted` event."""
    try:
        removed = await _guard(more.revert_session(svc, session_id, body.to_idx))
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return RevertResult(session_id=session_id, to_idx=body.to_idx, removed=removed)


@router.get("/api/shared/{token}", response_model=SharedSession, tags=["sessions"])
async def shared(token: str, svc: ServicesDep) -> SharedSession:
    """Public (no password): the token itself is the capability. 404 when unknown or revoked."""
    view = await more.shared_view(svc, token)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "share link not found or revoked")
    return SharedSession.model_validate(view)


router.include_router(authed)
