"""LiveKit token endpoint and webhook receiver (spec §26.9/§26.10, I8).

`POST /api/livekit/token` (AUOREO auth) is the server side of the browser's
`TokenSource.endpoint(...)`: the secret never leaves the server; the identity is a server-minted
uuid4; the token is least-grant for one room this server created (`room_join` + publish/
subscribe; TTL `livekit.token_ttl_s` ≤ 900 s); only explicit agent dispatch to the configured
worker (`i8.livekit.worker.agent_name`) is accepted in `room_config`. Audited as
`livekit.token_minted{session_id, room_hash, identity_hash, ttl_s, grants, outcome}` — never the JWT.

`POST /livekit/webhook` is called by the LiveKit server, not the browser, so it carries no
AUOREO auth: the `Authorization` header is a JWT signed with the API secret whose `sha256`
claim must match the RAW body (compared with `hmac.compare_digest`). Missing key/secret →
403 (fail-closed); bad signature → 401; redelivered event id (1 h cache) → 200 `duplicate`;
more than `i8.livekit.webhook_rate_per_min` per minute (after auth) → 429.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
import uuid
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep
from auoreo.core.services import Services
from auoreo.core.settings_i8 import i8_of
from auoreo.schema.livekit import LiveKitToken, LiveKitTokenRequest, LiveKitWebhookAck

router = APIRouter(tags=["livekit"])
MAX_WEBHOOK_BYTES = 1024 * 1024


def _hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest()[:16] if value else None


def _livekit(svc: Services) -> Any:
    try:
        inst = svc.backend("rtc_transport")
    except (KeyError, LookupError) as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from None
    if getattr(inst, "name", "") != "livekit":
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "backends.rtc_transport is not 'livekit'")
    if hasattr(inst, "bind"):
        inst.bind(svc)
    return inst


def _check_extras(body: LiveKitTokenRequest, svc: Services) -> list[str]:
    cfg = i8_of(svc.settings).livekit
    size = len((body.participant_metadata or "").encode())
    size += sum(len(k.encode()) + len(v.encode()) for k, v in (body.participant_attributes or {}).items())
    if size > cfg.max_metadata_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"participant metadata/attributes exceed {cfg.max_metadata_bytes} bytes (minimal, no PII)",
        )
    agents = [a.agent_name for a in (body.room_config.agents if body.room_config else [])]
    allowed = cfg.worker.agent_name
    if any(a != allowed for a in agents):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"only agent {allowed!r} may be dispatched")
    return agents


@router.post(
    "/api/livekit/token",
    dependencies=[*AUTH, IDEMPOTENT],
    response_model=LiveKitToken,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Disallowed or oversized fields"},
        403: {"description": "Unknown room, undispatchable agent, or LiveKit Cloud without consent"},
        404: {"description": "Unknown session"},
        501: {"description": "LiveKit transport not selected, SDK missing or not configured"},
        502: {"description": "The LiveKit server API failed"},
    },
)
async def token(body: LiveKitTokenRequest, svc: ServicesDep) -> LiveKitToken:
    """Mint a room token for the browser (`TokenSource.endpoint`); the JWT is never logged."""
    if body.session_id is not None and await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    lk = _livekit(svc)
    agents = _check_extras(body, svc)
    room = body.room_name
    try:
        if room is not None:
            known = lk.rooms.get(room)
            if known is None or (body.session_id and known.get("session_id") not in (None, body.session_id)):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "room_name is not a room of this server/session"
                )
        else:
            info = await lk.join(session_id=body.session_id, identity=str(uuid.uuid4()), offer_sdp=None)
            room = str(info.room)
        identity = str(uuid.uuid4())
        jwt = lk.mint_token(
            identity,
            room,
            name=body.participant_name,
            metadata=body.participant_metadata,
            attributes=body.participant_attributes,
            agents=agents,
        )
        url = lk._url()
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ConnectionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from None
    ttl = min(int(svc.settings.livekit.token_ttl_s), 900)
    await svc.audit.record(
        actor="client",
        action="livekit.token_minted",
        session_id=body.session_id,
        detail={
            "room_hash": _hash(room),
            "identity_hash": _hash(identity),
            "ttl_s": ttl,
            "grants": ["room_join", "can_publish", "can_subscribe"],
            "agents": agents,
            "outcome": "ok",
        },
    )
    return LiveKitToken(
        server_url=url, participant_token=jwt, room_name=room, participant_identity=identity, expires_in=ttl
    )


class WebhookState:
    def __init__(self) -> None:
        self.seen: dict[str, float] = {}
        self.hits: deque[float] = deque()

    def duplicate(self, event_id: str, ttl: float, now: float) -> bool:
        for k in [k for k, ts in self.seen.items() if now - ts > ttl]:
            self.seen.pop(k, None)
        if event_id in self.seen:
            return True
        self.seen[event_id] = now
        return False

    def limited(self, per_min: int, now: float) -> bool:
        while self.hits and now - self.hits[0] > 60:
            self.hits.popleft()
        if len(self.hits) >= per_min:
            return True
        self.hits.append(now)
        return False


def verify(raw: bytes, auth: str | None, key: str, secret: str) -> Any:
    """Verify the webhook JWT and its `sha256` body claim; returns the parsed `WebhookEvent`."""
    from google.protobuf.json_format import Parse
    from livekit import api

    if not auth:
        raise PermissionError("missing Authorization")
    token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else auth
    claims = api.TokenVerifier(key, secret).verify(token)
    try:
        claimed = base64.b64decode(claims.sha256 or "", validate=True)
    except (binascii.Error, ValueError):
        raise PermissionError("bad sha256 claim") from None
    if not claimed or not hmac.compare_digest(hashlib.sha256(raw).digest(), claimed):
        raise PermissionError("body hash mismatch")
    return Parse(raw.decode("utf-8"), api.WebhookEvent(), ignore_unknown_fields=True)


@router.post(
    "/livekit/webhook",
    response_model=LiveKitWebhookAck,
    responses={
        401: {"description": "Signature or body hash invalid"},
        403: {"description": "LiveKit API key/secret not configured (fail-closed)"},
        404: {"description": "Webhook receiver disabled"},
        413: {"description": "Body over 1 MiB"},
        429: {"description": "Rate limit (after authentication)"},
    },
)
async def webhook(request: Request, svc: ServicesDep) -> LiveKitWebhookAck:
    """LiveKit server → AUOREO room events (`application/webhook+json`), verified on the raw body."""
    cfg = i8_of(svc.settings).livekit
    if not cfg.webhook_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "livekit webhook receiver disabled")
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "webhook body too large")
    lk_cfg = svc.settings.livekit
    key, secret = svc.secrets.get(lk_cfg.api_key_secret), svc.secrets.get(lk_cfg.api_secret_secret)
    if not key or not secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "livekit API key/secret not configured")
    try:
        event = verify(raw, request.headers.get("authorization"), key, secret)
    except Exception:
        await svc.audit.record(actor="livekit", action="livekit.webhook", session_id=None, decision="denied")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature") from None
    state = svc.ext.setdefault("livekit_webhook_state", WebhookState())  # per-process dedupe/rate state
    now = time.monotonic()
    event_id = str(event.id or "")
    if event_id and state.duplicate(event_id, cfg.webhook_dedupe_s, now):
        return LiveKitWebhookAck(status="duplicate", event=event.event, id=event_id)
    if state.limited(cfg.webhook_rate_per_min, now):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "livekit webhook rate limit")
    room = event.room.name if event.HasField("room") else None
    session_id = None
    try:
        known = svc.backend("rtc_transport")
    except (KeyError, LookupError):
        known = None
    if room and isinstance(getattr(known, "rooms", None), dict):
        session_id = (known.rooms.get(room) or {}).get("session_id")
    data: dict[str, Any] = {"event": event.event, "id": event_id, "room_hash": _hash(room)}
    if event.HasField("participant"):
        data["participant_hash"] = _hash(event.participant.identity)
        data["participant_kind"] = int(event.participant.kind)
    if event.HasField("track"):
        data["track_type"] = int(event.track.type)
    await svc.emit(session_id or "server", "livekit.webhook", data)
    return LiveKitWebhookAck(status="ok", event=event.event, id=event_id)
