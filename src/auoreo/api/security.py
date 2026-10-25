"""HTTP control-plane hardening (spec §17, §24.7): Host check, idempotent POST replay and the
cookie-gated token injection into the SPA's `index.html`.

Middleware order (outermost first): Host check (400) → CORS → idempotency capture → routes.
Auth itself is the `require_auth` dependency (`deps.py`), so a bad Host never reaches it.
"""

from __future__ import annotations

import html
import json
import logging
import socket
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from auoreo.api.deps import COOKIE, IDEMPOTENCY_STATE, IdempotentReplay
from auoreo.core.idempotency import Admission
from auoreo.core.settings import Settings

log = logging.getLogger(__name__)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_BINDS = frozenset({"0.0.0.0", "::", ""})
MAX_STORED_BODY = 256 * 1024


# ------------------------------------------------------------------ Host header
def host_of(header: str) -> str:
    """`Host` value → bare lower-case host (`[::1]:4096` → `::1`, `a.lan:80` → `a.lan`)."""
    value = header.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    return value.rstrip(".")


def host_matches(host: str, pattern: str) -> bool:
    pat = pattern.strip().lower().strip("[]").rstrip(".")
    if pat == "*":
        return True
    if pat.startswith("*."):
        return host.endswith(pat[1:])
    return host == pat


@lru_cache(maxsize=1)
def _lan_names() -> frozenset[str]:
    """This machine's hostname + addresses (only consulted when bound to 0.0.0.0 / ::)."""
    names: set[str] = set()
    try:
        hostname = socket.gethostname()
        names |= {hostname.lower(), socket.getfqdn().lower()}
        names |= set(socket.gethostbyname_ex(hostname)[2])
    except OSError:
        pass
    return frozenset(names)


def _tunnel_host(home: Path) -> str | None:
    """The public host of a running `auoreo tunnel` (`<home>/tunnel.json`)."""
    try:
        url = json.loads((home / "tunnel.json").read_text(encoding="utf-8")).get("url")
    except (OSError, ValueError, AttributeError):
        return None
    return (urlsplit(url).hostname or None) if isinstance(url, str) else None


def host_allowed(host: str, settings: Settings) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    patterns = [settings.host, *settings.http.trusted_hosts]
    if any(host_matches(host, p) for p in patterns if p not in WILDCARD_BINDS):
        return True
    if settings.host in WILDCARD_BINDS and host in _lan_names():
        return True
    return host == _tunnel_host(settings.home)


class HostCheckMiddleware:
    """400 for a `Host` outside loopback / the bind host / `http.trusted_hosts` / the tunnel.
    Unix-socket requests (the daemon) carry no meaningful Host and are not checked."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self.settings.http.host_check:
            await self.app(scope, receive, send)
            return
        server = scope.get("server")
        if server is not None and server[1] is None and "/" in str(server[0]):  # UDS listener (a path)
            await self.app(scope, receive, send)
            return
        raw = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
        if raw and host_allowed(host_of(raw), self.settings):
            await self.app(scope, receive, send)
            return
        log.warning("rejected request with Host %r", raw[:100])
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "invalid host header"})
            return
        await send(
            {"type": "http.response.start", "status": 400, "headers": [(b"content-type", b"text/plain")]}
        )
        await send({"type": "http.response.body", "body": b"invalid host header"})


# ------------------------------------------------------------------ idempotency
class IdempotencyMiddleware:
    """Completes what the `idempotent` dependency admitted: a 2xx JSON response is stored
    for replay; anything else (error, exception, non-JSON) releases the claim."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT"}:
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        status = 500
        chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal status
            if state.get(IDEMPOTENCY_STATE) is not None:
                if message["type"] == "http.response.start":
                    status = int(message["status"])
                elif message["type"] == "http.response.body" and sum(map(len, chunks)) <= MAX_STORED_BODY:
                    chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            adm: Admission | None = state.pop(IDEMPOTENCY_STATE, None)
            if adm is not None:
                await _finish(scope, adm, status, b"".join(chunks))


async def _finish(scope: Scope, adm: Admission, status: int, raw: bytes) -> None:
    svc = scope["app"].state.services
    body: Any = None
    ok = 200 <= status < 300 and len(raw) <= MAX_STORED_BODY
    if ok:
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            ok = False
    try:
        if ok:
            await svc.idempotency.complete(adm.key, adm.route, status, body)
        else:
            await svc.idempotency.release(adm.key, adm.route)
    except Exception:
        log.exception("idempotency bookkeeping failed for %s", adm.route)


async def _replay(_request: Request, exc: Exception) -> JSONResponse:
    """A duplicate key: the stored body, `200`, `deduped: true`, never re-executed."""
    assert isinstance(exc, IdempotentReplay)
    body = exc.admission.body
    if isinstance(body, dict):
        body = {**body, "deduped": True}
    return JSONResponse(body, status_code=200, headers={"Idempotent-Replayed": "true"})


# ------------------------------------------------------------------ SPA token injection
def index_with_token(index: Path, scope: Scope, password: str | None) -> HTMLResponse | None:
    """`index.html` with `<meta name="auoreo-token">` — only for a request that already holds
    the valid HttpOnly cookie. Unauthenticated pages never get the token."""
    if not password:
        return None
    from auoreo.api.deps import token_matches

    cookie = Request(scope).cookies.get(COOKIE)
    if not cookie or not token_matches(password, cookie=cookie):
        return None
    try:
        text = index.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = f'<meta name="auoreo-token" content="{html.escape(password, quote=True)}">'
    text = text.replace("</head>", f"  {meta}\n  </head>", 1) if "</head>" in text else meta + text
    headers = {"Cache-Control": "no-store", "Vary": "Cookie", "Referrer-Policy": "same-origin"}
    return HTMLResponse(text, headers=headers)


def install(app: FastAPI, settings: Settings) -> None:
    """Call after CORS is added: Host check ends up outermost."""
    app.add_exception_handler(IdempotentReplay, _replay)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(HostCheckMiddleware, settings=settings)
