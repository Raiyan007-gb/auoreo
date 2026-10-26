"""Loopback egress proxy for `net: proxy` tools (spec §25.10 L1017/L1021).

One HTTP proxy per server on `127.0.0.1:<port>` (ephemeral by default), started by the
`auoreo.tools.egress:start` extension. It speaks `CONNECT host:port` (TLS tunnels) and
absolute-form plain-HTTP requests. Every request must carry `Proxy-Authorization: Basic`
with a live token: the executor mints one per tool call (revoked when the call ends, so the
connections it opened land in that call's audit row), MCP remote connections hold one per
server. For each tunnel the proxy:

1. refuses hosts on `i9.egress.deny_hosts`, hosts outside a non-empty `allow_hosts`, and
   ports outside a non-empty `allowed_ports`;
2. resolves the host ONCE and validates every address with the SSRF floor of
   `auoreo.tools.net` (private/loopback/link-local/CGNAT/metadata ranges blocked unless the
   host is on `allow_private_hosts` or is a configured search endpoint), then connects to
   that validated IP itself — the client never resolves, so DNS rebinding cannot redirect
   the tunnel (the pin lives here);
3. logs `token-label host:port -> ip verdict` (never payload) and records it per token.

A refusal answers `403` with `X-Auoreo-Egress: denied` and the reason as the body; the reason
is also kept as the token's `last_denial` so in-process clients can raise a clear error.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import fnmatch
import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from auoreo.tools.context import ToolError

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

log = logging.getLogger(__name__)

HEAD_LIMIT = 64 * 1024
CHUNK = 64 * 1024
USER = "auoreo"
DENIED_HEADER = "X-Auoreo-Egress"
_HOP = frozenset({"proxy-authorization", "proxy-connection", "connection", "keep-alive"})


@dataclass
class TokenLog:
    label: str
    conns: list[dict[str, Any]] = field(default_factory=list)
    last_denial: str | None = None


def _glob(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, p.lower()) for p in patterns)


def split_hostport(target: str, default: int = 443) -> tuple[str, int]:
    """`host:port` / `[v6]:port` → `(host, port)` (raises ValueError)."""
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default
    elif target.count(":") == 1:
        host, raw = target.split(":")
        port = int(raw)
    else:
        host, port = target, default
    if not host or not 0 < port < 65536:
        raise ValueError(f"bad target {target!r}")
    return host.lower(), port


def _response(status: str, reason: str = "", extra: str = "") -> bytes:
    body = reason.encode("utf-8", errors="replace")
    head = f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
    head += f"Content-Length: {len(body)}\r\nConnection: close\r\n{extra}\r\n"
    return head.encode("latin-1") + body


ACTIVE: list[EgressProxy] = []
"""Running proxies (normally one per process) so clients can look up a refusal reason."""


def denial_for(proxy_url: str) -> str | None:
    """The last refusal recorded for the token embedded in `proxy_url`."""
    token = urlsplit(proxy_url).password or ""
    for proxy in ACTIVE:
        if (entry := proxy.peek(token)) is not None:
            return entry.last_denial
    return None


def private_ok_for(proxy_url: str, host: str, port: int) -> bool:
    """Whether the proxy behind `proxy_url` lets `host:port` reach a private address."""
    token = urlsplit(proxy_url).password or ""
    return any(p.peek(token) is not None and p.private_ok(host, port) for p in ACTIVE)


class EgressProxy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._server: asyncio.base_events.Server | None = None
        self.port = 0
        self._tokens: dict[str, TokenLog] = {}
        self.stats = {"tunnels": 0, "denied": 0, "unauthorized": 0}

    # ------------------------------------------------------------------ lifecycle
    @property
    def cfg(self) -> Any:
        return self.settings.i9.egress

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.cfg.port, limit=HEAD_LIMIT)
        self.port = int(self._server.sockets[0].getsockname()[1])
        ACTIVE.append(self)
        log.info("egress proxy listening on 127.0.0.1:%d", self.port)

    async def close(self) -> None:
        if self in ACTIVE:
            ACTIVE.remove(self)
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
            self._server = None

    # ------------------------------------------------------------------ tokens
    def mint(self, label: str) -> str:
        token = secrets.token_urlsafe(18)
        self._tokens[token] = TokenLog(label)
        return token

    def revoke(self, token: str) -> TokenLog | None:
        return self._tokens.pop(token, None)

    def peek(self, token: str) -> TokenLog | None:
        return self._tokens.get(token)

    def url(self, token: str) -> str:
        return f"http://{USER}:{token}@127.0.0.1:{self.port}"

    def _auth(self, header: str | None) -> str | None:
        if not header or not header.lower().startswith("basic "):
            return None
        try:
            user, _, token = base64.b64decode(header[6:].strip()).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return token if user == USER and token in self._tokens else None

    # ------------------------------------------------------------------ policy
    def private_ok(self, host: str, port: int) -> bool:
        if getattr(self.settings, "allow_private_urls", False):
            return True
        pats = [p.lower() for p in self.cfg.allow_private_hosts]
        for url in (getattr(self.settings, "searxng_url", None), self.settings.i9.search.firecrawl_base_url):
            parts = urlsplit(url or "")
            if parts.hostname and parts.hostname != "api.firecrawl.dev":
                pats.append(f"{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}")
        return _glob(host, pats) or _glob(f"{host}:{port}", pats)

    def decide(self, host: str, port: int) -> str | None:
        """Why `host:port` is refused before any DNS lookup, or None."""
        cfg = self.cfg
        if _glob(host, cfg.deny_hosts):
            return f"host '{host}' is on i9.egress.deny_hosts"
        if cfg.allow_hosts and not _glob(host, cfg.allow_hosts):
            return f"host '{host}' is not on i9.egress.allow_hosts"
        if cfg.allowed_ports and port not in cfg.allowed_ports:
            return f"port {port} is not on i9.egress.allowed_ports"
        return None

    async def _admit(self, token: str, host: str, port: int) -> tuple[str | None, str | None]:
        """`(validated_ip, None)` or `(None, reason)`; recorded on the token either way."""
        from auoreo.tools.net import resolve_validated

        reason = self.decide(host, port)
        ip: str | None = None
        if reason is None:
            try:
                ip = await resolve_validated(host, port, self.private_ok(host, port))
            except ToolError as exc:
                reason = str(exc)
        entry = self._tokens.get(token)
        if entry is not None:
            entry.conns.append({"host": host, "port": port, "allowed": reason is None})
            if reason is not None:
                entry.last_denial = reason
        label = entry.label if entry else "?"
        if self.cfg.log_connections:
            log.info("egress %s %s:%d -> %s %s", label, host, port, ip or "-", reason or "allowed")
        if reason is not None:
            self.stats["denied"] += 1
        return ip, reason

    # ------------------------------------------------------------------ protocol
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.cfg.connect_timeout_s)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _version = lines[0].split(" ", 2)
            headers = [tuple(x.split(":", 1)) for x in lines[1:] if ":" in x]
            hmap = {k.strip().lower(): v.strip() for k, v in headers}
            token = self._auth(hmap.get("proxy-authorization"))
            if token is None:
                self.stats["unauthorized"] += 1
                writer.write(
                    _response(
                        "407 Proxy Authentication Required",
                        "auoreo egress proxy: missing or revoked token",
                        'Proxy-Authenticate: Basic realm="auoreo-egress"\r\n',
                    )
                )
                return
            connect = method.upper() == "CONNECT"
            if connect:
                host, port = split_hostport(target)
                path = ""
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    writer.write(_response("400 Bad Request", "absolute http:// URL or CONNECT required"))
                    return
                host, port = parts.hostname.lower(), parts.port or 80
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            ip, reason = await self._admit(token, host, port)
            if reason is not None or ip is None:
                writer.write(_response("403 Forbidden", reason or "refused", f"{DENIED_HEADER}: denied\r\n"))
                return
            try:
                up_reader, up_writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), self.cfg.connect_timeout_s
                )
            except (OSError, TimeoutError) as exc:
                writer.write(_response("502 Bad Gateway", f"cannot reach {host}:{port}: {exc}"))
                return
            upstream = up_writer
            self.stats["tunnels"] += 1
            if connect:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._splice(reader, writer, up_reader, up_writer)
                return
            out = [f"{method} {path} HTTP/1.1"]
            out += [f"{k.strip()}: {v.strip()}" for k, v in headers if k.strip().lower() not in _HOP]
            out.append("Connection: close")
            up_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            await up_writer.drain()
            await self._splice(reader, writer, up_reader, up_writer, close_response=True)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, TimeoutError):
            with contextlib.suppress(Exception):
                writer.write(_response("400 Bad Request", "malformed proxy request"))
        except (ConnectionError, OSError):
            pass
        finally:
            for w in (writer, upstream):
                if w is not None:
                    with contextlib.suppress(Exception):
                        await w.drain()
                    w.close()

    async def _splice(
        self,
        c_reader: asyncio.StreamReader,
        c_writer: asyncio.StreamWriter,
        u_reader: asyncio.StreamReader,
        u_writer: asyncio.StreamWriter,
        *,
        close_response: bool = False,
    ) -> None:
        idle = self.cfg.idle_timeout_s

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, rewrite: bool) -> None:
            if rewrite:  # plain HTTP: force `Connection: close` so the client never reuses it
                head = await asyncio.wait_for(src.readuntil(b"\r\n\r\n"), idle)
                lines = [x for x in head.decode("latin-1").split("\r\n") if x]
                kept = [lines[0], *(x for x in lines[1:] if x.split(":", 1)[0].strip().lower() not in _HOP)]
                dst.write(("\r\n".join([*kept, "Connection: close"]) + "\r\n\r\n").encode("latin-1"))
            while data := await asyncio.wait_for(src.read(CHUNK), idle):
                dst.write(data)
                await dst.drain()

        tasks = [
            asyncio.create_task(pump(c_reader, u_writer, False)),
            asyncio.create_task(pump(u_reader, c_writer, close_response)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "port": self.port, "tokens": len(self._tokens), **self.stats}


def session_proxy(label: str) -> tuple[dict[str, str], str] | None:
    """A long-lived token for a connection that outlives one tool call (a browser context, an
    MCP remote server): `({server, username, password}, token)` or None when no proxy runs.
    Revoke the token with `revoke_session(token)` when the connection closes."""
    proxy = next((p for p in ACTIVE if p.running), None)
    if proxy is None:
        return None
    token = proxy.mint(label)
    return {"server": f"http://127.0.0.1:{proxy.port}", "username": USER, "password": token}, token


def revoke_session(token: str) -> None:
    for proxy in ACTIVE:
        proxy.revoke(token)


def proxy_of(services: Any) -> EgressProxy | None:
    ext = getattr(services, "ext", None) or {}
    proxy = ext.get("egress")
    return proxy if isinstance(proxy, EgressProxy) and proxy.running else None


async def ensure(services: Services) -> EgressProxy | None:
    """The server-wide proxy, started on first use (MCP connects before the extensions run)."""
    if (proxy := proxy_of(services)) is not None:
        return proxy
    if not services.settings.i9.egress.enabled:
        return None
    proxy = EgressProxy(services.settings)
    await proxy.start()
    services.ext["egress"] = proxy
    return proxy


async def start(services: Services) -> EgressProxy | None:
    """Extension starter: `services.ext["egress"]` (closed with the other extensions)."""
    return await ensure(services)
