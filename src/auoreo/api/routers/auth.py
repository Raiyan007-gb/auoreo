"""`/api/auth/login|logout|status|capability`: browser session tokens (spec §18, §24.7, §25.14).

`login` checks the server password and sets an HttpOnly cookie holding a short-lived HS256
JWT (`h1b.jwt_ttl_s` ≤ 15 min, `jti`, bound to the client IP) — never the password itself.
Cookie-authenticated requests slide it forward (`session_auth.schedule_refresh`); `logout`
revokes the login. `capability` mints a per-session `/ws` token for `--ws-auth capability`.
A cookie-authenticated unsafe request must also be same-origin (`deps.require_auth`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status

from auoreo.api.deps import AUTH, COOKIE, ServicesDep, token_matches
from auoreo.api.session_auth import client_ip, cookie_kwargs, is_https, principal
from auoreo.core.session_jwt import tokens_for
from auoreo.schema.auth import AuthStatus, CapabilityRequest, CapabilityToken, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=AuthStatus, responses={401: {"description": "Wrong password"}})
async def login(body: LoginRequest, request: Request, response: Response, svc: ServicesDep) -> AuthStatus:
    password = svc.settings.server_password
    if not password:
        return AuthStatus(secured=False, authenticated=True, method="open")
    if not token_matches(password, query_token=body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong password")
    token, claims = tokens_for(svc).mint(ip=client_ip(request))
    response.set_cookie(COOKIE, token, **cookie_kwargs(svc, is_https(request)))
    response.headers["Cache-Control"] = "no-store"
    await svc.audit.record(
        actor="human", action="auth.login", decision="allow", detail={"sid": claims.sid, "ip": claims.ip}
    )
    return AuthStatus(secured=True, authenticated=True, expires_at=claims.exp, method="jwt")


@router.post("/logout", response_model=AuthStatus)
async def logout(
    request: Request,
    response: Response,
    svc: ServicesDep,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> AuthStatus:
    """Revoke the cookie's login (every refreshed token of it) and clear the cookie."""
    who = principal(svc, cookie=auoreo_token, ip=client_ip(request)) if auoreo_token else None
    if who is not None and who.claims is not None:
        tokens_for(svc).revoke(who.claims)
        await svc.audit.record(actor="human", action="auth.logout", detail={"sid": who.claims.sid})
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=is_https(request))
    return AuthStatus(secured=bool(svc.settings.server_password), authenticated=False)


@router.get("/status", response_model=AuthStatus)
async def auth_status(
    request: Request,
    svc: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
) -> AuthStatus:
    who = principal(svc, authorization=authorization, cookie=auoreo_token, ip=client_ip(request))
    return AuthStatus(
        secured=bool(svc.settings.server_password),
        authenticated=who is not None,
        expires_at=who.claims.exp if who is not None and who.claims is not None else None,
        method=who.kind if who is not None else None,
    )


@router.post(
    "/capability",
    response_model=CapabilityToken,
    dependencies=AUTH,
    responses={404: {"description": "Unknown session"}},
)
async def capability(body: CapabilityRequest, request: Request, svc: ServicesDep) -> CapabilityToken:
    """A `/ws` token that only sees and steers `session_id` (`serve --ws-auth capability`)."""
    if await svc.get_session(body.session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    token, claims = tokens_for(svc).mint(ip=client_ip(request), kind="cap", session_id=body.session_id)
    await svc.audit.record(
        actor="human",
        action="auth.capability",
        session_id=body.session_id,
        detail={"sid": claims.sid, "exp": claims.exp},
    )
    return CapabilityToken(token=token, session_id=body.session_id, expires_at=claims.exp)
