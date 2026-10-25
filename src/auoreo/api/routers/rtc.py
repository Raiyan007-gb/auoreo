"""`/api/rtc` — WebRTC signaling (M4e). Media and the `auoreo-events` channel run on aiortc;
see `auoreo.rtc.peers`. Control (approvals, steer, interrupt) stays on `/ws`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.rtc.peers import RtcLimitError, RtcManager, RtcUnavailable, SdpTooLarge
from auoreo.schema.chat import RtcAnswer, RtcClosed, RtcOffer, RtcPeerInfo

router = APIRouter(prefix="/api/rtc", tags=["rtc"], dependencies=AUTH)

# First match wins (SdpTooLarge is a ValueError, so it comes first).
_ERRORS: dict[type[Exception], int] = {
    SdpTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    RtcUnavailable: status.HTTP_501_NOT_IMPLEMENTED,
    RtcLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    ValueError: status.HTTP_400_BAD_REQUEST,
}


def manager(svc: Services) -> RtcManager:
    if svc.rtc is None:
        svc.rtc = RtcManager(svc)
    mgr: RtcManager = svc.rtc
    return mgr


@router.post(
    "/offer",
    response_model=RtcAnswer,
    status_code=status.HTTP_201_CREATED,
    responses={
        413: {"description": "SDP over 64 KiB"},
        429: {"description": "Too many peers (`rtc_max_peers`)"},
        501: {"description": "aiortc not installed: `install auoreo[rtc]`"},
    },
)
async def offer(body: RtcOffer, svc: ServicesDep) -> RtcAnswer:
    """Answer an SDP offer (non-trickle: send the offer after ICE gathering completes)."""
    if body.session_id is not None and await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    try:
        rtc_id, sdp = await manager(svc).offer(body.sdp, body.type, body.session_id)
    except tuple(_ERRORS) as exc:
        code = next(c for t, c in _ERRORS.items() if isinstance(exc, t))
        raise HTTPException(code, str(exc)) from None
    return RtcAnswer(rtc_id=rtc_id, sdp=sdp)


@router.delete("/{rtc_id}", response_model=RtcClosed)
async def close(rtc_id: str, svc: ServicesDep) -> RtcClosed:
    """Idempotent: 200 with `closed: false` for an unknown or already-closed peer."""
    return RtcClosed(closed=await manager(svc).close(rtc_id))


@router.get("", response_model=list[RtcPeerInfo])
async def list_peers(svc: ServicesDep) -> list[RtcPeerInfo]:
    return [RtcPeerInfo(**p) for p in manager(svc).describe()]
