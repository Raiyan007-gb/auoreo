"""`/api/rtc` — WebRTC signaling (M4e). Media and the `auoreo-events` channel run on aiortc;
see `auoreo.rtc.peers`. Control (approvals, steer, interrupt) stays on `/ws`.

`POST /api/rtc/join` (W2-D) is transport-agnostic: the selected `rtc_transport` backend
(`aiortc` by default, `livekit` opt-in) answers with an SDP or a room URL + token."""

from __future__ import annotations

import uuid
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Response, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.core.services import Services
from auoreo.rtc.peers import (
    RtcLimitError,
    RtcManager,
    RtcUnavailable,
    SdpTooLarge,
    aiortc_available,
    manager_for,
)
from auoreo.rtc.turn import ice_servers
from auoreo.schema.chat import RtcAnswer, RtcClosed, RtcPeerInfo
from auoreo.schema.rtc_transport import (
    RtcIceConfig,
    RtcIceServer,
    RtcJoinInfo,
    RtcJoinRequest,
    RtcLeft,
    RtcOffer,
)
from auoreo.schema.ui import RtcCapabilities

router = APIRouter(prefix="/api/rtc", tags=["rtc"], dependencies=AUTH)

# First match wins (SdpTooLarge is a ValueError, so it comes first).
_ERRORS: dict[type[Exception], int] = {
    SdpTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    RtcUnavailable: status.HTTP_501_NOT_IMPLEMENTED,
    RtcLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    ValueError: status.HTTP_400_BAD_REQUEST,
    PermissionError: status.HTTP_403_FORBIDDEN,  # F1: `input: true` without teleop_input_enabled
}


def manager(svc: Services) -> RtcManager:
    return manager_for(svc)


@router.post(
    "/offer",
    dependencies=[IDEMPOTENT],
    response_model=RtcAnswer,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "`input: true` while `settings.teleop_input_enabled` is false"},
        413: {"description": "SDP over 64 KiB"},
        429: {"description": "Too many peers (`rtc_max_peers`)"},
        501: {"description": "aiortc not installed: `install auoreo[rtc]`"},
    },
)
async def offer(body: RtcOffer, svc: ServicesDep) -> RtcAnswer:
    """Answer an SDP offer (non-trickle: send the offer after ICE gathering completes)."""
    if body.input and not svc.settings.teleop_input_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "teleop input is disabled")
    if body.session_id is not None and await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    try:
        rtc_id, sdp = await manager(svc).offer(
            body.sdp, body.type, body.session_id, speak=body.speak, input_=body.input
        )
    except tuple(_ERRORS) as exc:
        code = next(c for t, c in _ERRORS.items() if isinstance(exc, t))
        raise HTTPException(code, str(exc)) from None
    return RtcAnswer(rtc_id=rtc_id, sdp=sdp)


@router.get("/ice", response_model=RtcIceConfig)
async def ice(svc: ServicesDep, response: Response) -> RtcIceConfig:
    """ICE servers for a browser `RTCPeerConnection` (F5): `rtc_ice_servers` plus, when
    `turn_secret_ref` + `turn_urls` are set, TURN REST credentials valid `turn_ttl_s` seconds.
    The shared secret is never returned; fetch this right before each offer."""
    response.headers["Cache-Control"] = "no-store"  # credentials: never cache
    servers, expires = ice_servers(svc.settings, svc.secrets)
    return RtcIceConfig(
        iceServers=[RtcIceServer(**s) for s in servers],
        expires_at=expires,
        ttl_s=int(svc.settings.turn_ttl_s) if expires else None,
    )


@router.get("/capabilities", response_model=RtcCapabilities)
async def capabilities(svc: ServicesDep) -> RtcCapabilities:
    """What the browser call client may request (G5b): the "allow remote control" switch asks
    for `input: true` only when `teleop_input_enabled` is reported here."""
    st = svc.settings
    return RtcCapabilities(
        available=aiortc_available(),
        voice_enabled=bool(st.voice_enabled),
        speak=bool(st.voice_enabled and st.voice_speak_over_rtc),
        teleop_input_enabled=bool(st.teleop_input_enabled),
    )


@router.delete("/{rtc_id}", response_model=RtcClosed)
async def close(rtc_id: str, svc: ServicesDep) -> RtcClosed:
    """Idempotent: 200 with `closed: false` for an unknown or already-closed peer."""
    return RtcClosed(closed=await manager(svc).close(rtc_id))


@router.get("", response_model=list[RtcPeerInfo])
async def list_peers(svc: ServicesDep) -> list[RtcPeerInfo]:
    return [RtcPeerInfo(**p) for p in manager(svc).describe()]


# --- W2-D: transport-agnostic join (backends/rtc_transport/<name>)
_JOIN_ERRORS: dict[type[Exception], int] = {
    **_ERRORS,
    PermissionError: status.HTTP_403_FORBIDDEN,  # e.g. LiveKit Cloud without cloud_consent
    ConnectionError: status.HTTP_502_BAD_GATEWAY,
    LookupError: status.HTTP_501_NOT_IMPLEMENTED,  # no rtc_transport backend selected
    RuntimeError: status.HTTP_501_NOT_IMPLEMENTED,  # SDK missing / not configured
}


def _raise(exc: Exception) -> NoReturn:
    code = next(c for t, c in _JOIN_ERRORS.items() if isinstance(exc, t))
    raise HTTPException(code, str(exc)) from None


def transport(svc: Services) -> Any:
    inst = svc.backend("rtc_transport")
    if hasattr(inst, "bind"):
        inst.bind(svc)
    return inst


@router.post(
    "/join",
    dependencies=[IDEMPOTENT],
    response_model=RtcJoinInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "LiveKit Cloud URL without `settings.livekit.cloud_consent`"},
        429: {"description": "Too many peers (`rtc_max_peers`)"},
        501: {"description": "Transport not installed or not configured"},
        502: {"description": "The SFU's server API failed"},
    },
)
async def join(body: RtcJoinRequest, svc: ServicesDep) -> RtcJoinInfo:
    """Join the media session for `session_id` through the selected `rtc_transport` backend.
    The token in the answer is never logged or audited."""
    if body.session_id is not None and await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    try:
        inst = transport(svc)
        identity = str(uuid.uuid4())  # I1 §26.10: server-minted, never the client value
        info = await inst.join(session_id=body.session_id, identity=identity, offer_sdp=body.sdp)
    except tuple(_JOIN_ERRORS) as exc:
        _raise(exc)
    join_id = str(info.extra.get("join_id") or info.room or "")
    await svc.audit.record(
        actor="client",
        action="rtc.join",
        session_id=body.session_id,
        detail={"transport": info.transport, "join_id": join_id, "room": info.room},
    )
    return RtcJoinInfo(
        transport=info.transport,
        join_id=join_id,
        session_id=info.session_id,
        sdp=info.sdp,
        url=info.url,
        token=info.token,
        room=info.room,
        extra=dict(info.extra),
    )


@router.delete("/join/{join_id}", response_model=RtcLeft)
async def leave(join_id: str, svc: ServicesDep) -> RtcLeft:
    """Idempotent leave through the selected transport."""
    try:
        inst = transport(svc)
        await inst.leave(join_id)
    except tuple(_JOIN_ERRORS) as exc:
        _raise(exc)
    return RtcLeft(transport=str(inst.name), join_id=join_id)
