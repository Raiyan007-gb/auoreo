"""Shared FastAPI dependencies: services lookup and token auth."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Query, Request, status

from auoreo.core.services import Services

COOKIE = "auoreo_token"


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Bearer header → `?token=` (GET event stream only) → cookie. Constant-time compare."""
    svc = get_services(request)
    expected = svc.settings.server_password
    if not expected:
        return
    supplied: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    elif token and request.method == "GET" and request.url.path == "/api/events":
        supplied = token
    elif auoreo_token:
        supplied = auoreo_token
    if supplied is None or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )


ServicesDep = Annotated[Services, Depends(get_services)]
AUTH = [Depends(require_auth)]
