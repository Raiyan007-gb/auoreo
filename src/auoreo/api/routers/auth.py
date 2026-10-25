"""`/api/auth/login|logout|status`: the HttpOnly session cookie for browsers.

The cookie (`HttpOnly; SameSite=Lax; Secure` over https) authenticates `GET /api/events`
(EventSource cannot set headers), `/ws` and the SPA's `index.html` token injection. A
cookie-authenticated unsafe request must also be same-origin (`deps.require_auth`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status

from auoreo.api.deps import COOKIE, ServicesDep, token_matches
from auoreo.schema.auth import AuthStatus, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() in {"https", "wss"}


@router.post("/login", response_model=AuthStatus, responses={401: {"description": "Wrong password"}})
async def login(body: LoginRequest, request: Request, response: Response, svc: ServicesDep) -> AuthStatus:
    password = svc.settings.server_password
    if not password:
        return AuthStatus(secured=False, authenticated=True)
    if not token_matches(password, cookie=body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong password")
    response.set_cookie(
        COOKIE,
        password,
        max_age=svc.settings.http.cookie_max_age_s,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_https(request),
    )
    return AuthStatus(secured=True, authenticated=True)


@router.post("/logout", response_model=AuthStatus)
async def logout(request: Request, response: Response, svc: ServicesDep) -> AuthStatus:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=_https(request))
    return AuthStatus(secured=bool(svc.settings.server_password), authenticated=False)


@router.get("/status", response_model=AuthStatus)
async def auth_status(
    svc: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> AuthStatus:
    password = svc.settings.server_password
    ok = token_matches(password, authorization=authorization, cookie=auoreo_token)
    return AuthStatus(secured=bool(password), authenticated=ok)
