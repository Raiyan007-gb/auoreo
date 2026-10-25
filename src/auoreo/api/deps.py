"""Shared FastAPI dependencies: services lookup and token auth."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Query, Request, status

from auoreo.core.services import Services

COOKIE = "auoreo_token"


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def token_matches(
    expected: str | None,
    *,
    authorization: str | None = None,
    query_token: str | None = None,
    cookie: str | None = None,
) -> bool:
    """Bearer header → `?token=` (only where the caller allows it) → cookie.
    Constant-time compare; no password configured means open."""
    if not expected:
        return True
    supplied: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    elif query_token:
        supplied = query_token
    elif cookie:
        supplied = cookie
    return supplied is not None and secrets.compare_digest(supplied.encode(), expected.encode())


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Bearer header → `?token=` (GET event stream only) → cookie. Constant-time compare."""
    svc = get_services(request)
    query_ok = request.method == "GET" and request.url.path == "/api/events"
    if not token_matches(
        svc.settings.server_password,
        authorization=authorization,
        query_token=token if query_ok else None,
        cookie=auoreo_token,
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )


ServicesDep = Annotated[Services, Depends(get_services)]
AUTH = [Depends(require_auth)]
