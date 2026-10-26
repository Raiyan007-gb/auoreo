"""Shared FastAPI dependencies: services lookup, token auth (+ cookie CSRF) and POST idempotency."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from auoreo.api.session_auth import COOKIE as COOKIE  # re-export
from auoreo.api.session_auth import (
    bearer_of,
    client_ip,
    password_matches,
    principal,
    schedule_refresh,
)
from auoreo.core.idempotency import Admission, valid_key
from auoreo.core.services import Services

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
IDEMPOTENCY_STATE = "auoreo_idempotency"
"""`scope["state"]` key: the admission `IdempotencyMiddleware` completes or releases."""


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def token_matches(
    expected: str | None,
    *,
    authorization: str | None = None,
    query_token: str | None = None,
    cookie: str | None = None,
) -> bool:
    """Password check only (Bearer → `?token=`), constant-time; no password means open.
    The `cookie` argument is ignored: the browser cookie is a session JWT (`session_auth`)."""
    del cookie
    if not expected:
        return True
    supplied = bearer_of(authorization) or query_token
    return password_matches(expected, supplied)


def same_origin(request: Request, extra: list[str]) -> bool:
    """CSRF guard for cookie-authenticated unsafe methods: `Origin` (else `Referer`) must name
    this host, or an explicitly allowed origin."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        return False
    parts = urlsplit(source)
    origin = f"{parts.scheme}://{parts.netloc}"
    return parts.netloc.lower() == request.headers.get("host", "").lower() or origin in extra


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Bearer (server password or session JWT) → HttpOnly session-JWT cookie. `?token=` is
    never accepted here (WS handshake only). A cookie-authenticated POST/PUT/PATCH/DELETE
    also needs a same-origin `Origin`/`Referer` (CSRF)."""
    svc = get_services(request)
    who = principal(svc, authorization=authorization, cookie=auoreo_token, ip=client_ip(request))
    if who is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.scope.setdefault("state", {})["auoreo_principal"] = who
    schedule_refresh(request, svc, who)  # --- H1b: sliding session cookie
    cookie_write = who.via == "cookie" and request.method not in SAFE_METHODS
    if cookie_write and not same_origin(request, svc.settings.cors_origins):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-origin request with cookie auth")


# ------------------------------------------------------------------ idempotency
class IdempotentReplay(Exception):
    """Raised for a duplicate key; the app's handler answers with the stored response."""

    def __init__(self, admission: Admission) -> None:
        super().__init__(admission.route)
        self.admission = admission


async def admit_request(request: Request, key: str) -> None:
    """Claim `key` for this route: fresh → run (the middleware stores the response),
    duplicate → `IdempotentReplay`, still running → 409. Non-UUID → 400."""
    if not valid_key(key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "idempotency key must be a UUID")
    route = getattr(request.scope.get("route"), "path", request.url.path)
    adm = await get_services(request).idempotency.admit(key, f"{request.method} {route}")
    if adm.state == "duplicate":
        raise IdempotentReplay(adm)
    if adm.state == "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "a request with this Idempotency-Key is in progress")
    request.scope.setdefault("state", {})[IDEMPOTENCY_STATE] = adm


async def idempotent(
    request: Request,
    idempotency_key: Annotated[
        str | None, Header(description="UUID; a retry with the same key replays the first response")
    ] = None,
) -> None:
    if idempotency_key is not None:
        await admit_request(request, idempotency_key)


ServicesDep = Annotated[Services, Depends(get_services)]
AUTH = [Depends(require_auth)]
IDEMPOTENT = Depends(idempotent)
"""`@router.post(..., dependencies=[IDEMPOTENT])` on every create-style POST."""
