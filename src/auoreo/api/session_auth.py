"""Who is calling (spec §5.3, §18, §24.7): the server password as a Bearer token (API clients),
a short-lived session JWT (the browser cookie, or a Bearer), or a per-session capability token
(`/ws` under `--ws-auth capability`). The cookie is never the raw password.

`principal()` is the one place credentials are checked (constant-time for the password);
`deps.require_auth`, `/ws`, `/mcp` and the SPA token injection all call it. A JWT cookie past
`h1b.jwt_refresh_after_s` is re-minted on the response by `RefreshCookieMiddleware`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from auoreo.core.session_jwt import Claims, Kind, looks_like_jwt, tokens_for

COOKIE = "auoreo_token"
REFRESH_STATE = "auoreo_refresh_cookie"
PrincipalKind = Literal["open", "password", "jwt", "cap"]


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    via: Literal["none", "bearer", "query", "cookie"] = "none"
    claims: Claims | None = None


def bearer_of(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def password_matches(expected: str | None, supplied: str | None) -> bool:
    if not expected or supplied is None:
        return False
    return secrets.compare_digest(supplied.encode(), expected.encode())


def client_ip(conn: HTTPConnection | Scope) -> str | None:
    client = (conn.scope if isinstance(conn, HTTPConnection) else conn).get("client")
    return str(client[0]) if client else None


def principal(
    svc: Any,
    *,
    authorization: str | None = None,
    query_token: str | None = None,
    cookie: str | None = None,
    ip: str | None = None,
    kind: Kind = "session",
) -> Principal | None:
    """Bearer → `?token=` (callers pass it only for the WS handshake) → cookie. Bearer and
    query accept the password or a token of `kind`; the cookie accepts only a session JWT.
    No password configured → `open`."""
    password = svc.settings.server_password
    if not password:
        return Principal("open")
    tokens = tokens_for(svc)
    for via, value in (("bearer", bearer_of(authorization)), ("query", query_token)):
        if value is None:
            continue
        if password_matches(password, value):
            return Principal("password", via)  # type: ignore[arg-type]
        if looks_like_jwt(value) and (claims := tokens.verify(value, ip=ip, kind=kind)) is not None:
            return Principal("cap" if kind == "cap" else "jwt", via, claims)  # type: ignore[arg-type]
        return None  # a wrong explicit credential never falls through to the cookie
    if cookie and (claims := tokens.verify(cookie, ip=ip)) is not None:
        return Principal("jwt", "cookie", claims)
    return None


def cookie_kwargs(svc: Any, https: bool) -> dict[str, Any]:
    return {
        "max_age": svc.settings.h1b.jwt_ttl_s,
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": https,
    }


def is_https(conn: HTTPConnection) -> bool:
    proto = conn.headers.get("x-forwarded-proto", conn.url.scheme)
    return proto.split(",")[0].strip().lower() in {"https", "wss"}


def schedule_refresh(conn: HTTPConnection, svc: Any, who: Principal) -> None:
    """Mark a cookie-authenticated request whose JWT is old enough for a fresh one."""
    if who.via != "cookie" or who.claims is None or not tokens_for(svc).needs_refresh(who.claims):
        return
    token, _ = tokens_for(svc).refresh(who.claims, client_ip(conn))
    from starlette.responses import Response

    probe = Response()
    probe.set_cookie(COOKIE, token, **cookie_kwargs(svc, is_https(conn)))
    conn.scope.setdefault("state", {})[REFRESH_STATE] = probe.headers["set-cookie"]


class RefreshCookieMiddleware:
    """Appends the `Set-Cookie` that `schedule_refresh` prepared (any response type, SSE too)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                header = (scope.get("state") or {}).pop(REFRESH_STATE, None)
                if header:
                    message = {
                        **message,
                        "headers": [*message.get("headers", []), (b"set-cookie", header.encode())],
                    }
            await send(message)

        await self.app(scope, receive, wrapped)


# ------------------------------------------------------------------ /ws (spec §18 --ws-auth)
WS_KIND: dict[str, Kind] = {"token": "session", "jwt": "session", "capability": "cap"}
WS_ACCEPTS: dict[str, frozenset[str]] = {
    "token": frozenset({"password", "jwt", "open"}),
    "jwt": frozenset({"jwt", "open"}),
    "capability": frozenset({"cap", "open"}),
}


def ws_principal(
    svc: Any,
    *,
    authorization: str | None,
    query_token: str | None,
    cookie: str | None,
    ip: str | None,
) -> Principal | None:
    """The handshake principal under `cli.ws_auth`, or None (reject with 1008)."""
    mode = svc.settings.cli.ws_auth
    if mode == "off":
        return Principal("open")
    kind = WS_KIND.get(mode, "session")
    who = principal(
        svc,
        authorization=authorization,
        query_token=query_token,
        cookie=None if kind == "cap" else cookie,
        ip=ip,
        kind=kind,
    )
    return who if who is not None and who.kind in WS_ACCEPTS.get(mode, frozenset()) else None


def ws_admit(
    conn: HTTPConnection, svc: Any, authorization: str | None, query_token: str | None, cookie: str | None
) -> bool:
    """Handshake check; on success the principal (and the password it was checked against)
    is kept in `scope["state"]` for `WsConnection`."""
    who = ws_principal(
        svc, authorization=authorization, query_token=query_token, cookie=cookie, ip=client_ip(conn)
    )
    if who is not None:
        state = conn.scope.setdefault("state", {})
        state.update(auoreo_password=svc.settings.server_password, auoreo_principal=who)
    return who is not None


def ws_still_valid(conn: Any) -> tuple[int, str] | None:
    """`(close code, reason)` once the handshake credentials stop being valid: the password
    changed (4401), the token was revoked (4401) or it expired (4001)."""
    from auoreo.schema.ws import CLOSE_AUTH_EXPIRED, CLOSE_REVOKED

    svc = conn.svc
    who: Principal | None = getattr(conn, "principal", None)
    if svc.settings.server_password != conn.password:
        return CLOSE_REVOKED, "credentials revoked"
    if who is None or who.claims is None:
        return None
    tokens = tokens_for(svc)
    if who.claims.sid in tokens.revoked or who.claims.jti in tokens.revoked:
        return CLOSE_REVOKED, "credentials revoked"
    if not tokens.is_live(who.claims):  # --- I1: cookie JWTs expire too; the SPA reconnects
        return CLOSE_AUTH_EXPIRED, "authentication expired; reconnect"  # with its refreshed cookie
    return None


def ws_scope_session(conn: Any) -> str | None:
    """The one session a capability-authenticated connection may see and steer."""
    who: Principal | None = getattr(conn, "principal", None)
    return who.claims.session_id if who is not None and who.kind == "cap" and who.claims else None


def ws_channels(conn: Any, channels: frozenset[str] | None) -> frozenset[str] | None:
    """Capability connections only ever subscribe to `session:<their id>`."""
    sid = ws_scope_session(conn)
    if sid is None:
        return channels
    mine = f"session:{sid}"
    return frozenset({mine}) if channels is None or mine in channels else frozenset()
