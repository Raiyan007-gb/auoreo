"""SSRF-safe HTTP helpers shared by network tools (web_fetch, http_request, image URLs).

DNS pinning (G4b, spec §9.3 "resolve+validate+pin"): `PinnedTransport` resolves each host
once, rejects it if *any* address is private/loopback/link-local/reserved/multicast/
non-global, then connects to the validated IP itself — the URL is rewritten to the IP, the
`Host` header keeps the name and TLS uses it as SNI and for certificate verification
(httpx `sni_hostname` extension). A second resolution (DNS rebinding) can never redirect
the connection to an internal address. Clients built here ignore proxy env vars
(`trust_env=False`): a proxy would resolve the name again and defeat the pin.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from auoreo.tools.context import ToolError

MAX_REDIRECTS = 5
IP = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]


def _blocked(ip: IP) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked(ip.ipv4_mapped)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global  # CGNAT 100.64/10, benchmarking, documentation ranges, ...
    )


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve host '{host}': {exc}") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


RESOLVER: list[Resolver] = [system_resolve]
"""Indirection for tests (`RESOLVER[0] = fake`)."""


async def resolve_validated(host: str, port: int, allow_private: bool = False) -> str:
    """The IP to connect to for `host` (resolved once; every address checked)."""
    host = host.strip("[]")
    try:
        literal: IP | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addrs = [str(literal)] if literal is not None else await RESOLVER[0](host, port)
    if not addrs:
        raise ToolError(f"cannot resolve host '{host}': no addresses")
    if not allow_private:
        for raw in addrs:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
            if _blocked(ip):
                raise ToolError(f"blocked address {ip} for host '{host}' (SSRF guard)")
    return addrs[0]


def check_shape(url: str) -> tuple[str, int]:
    """Reject non-http(s) URLs, missing hosts and inline credentials; `(host, port)`."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ToolError(f"only http/https URLs are allowed (got '{parts.scheme}')")
    if not parts.hostname:
        raise ToolError("URL has no host")
    if parts.username or parts.password:
        raise ToolError("credentials in URLs are not allowed")
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


async def check_url(url: str, allow_private: bool = False) -> None:
    """Shape check + resolve-and-validate (no connection). Used where the connection is made
    by someone else (e.g. the browser backend); HTTP clients here also pin the address."""
    host, port = check_shape(url)
    await resolve_validated(host, port, allow_private)


class PinnedTransport(httpx.AsyncBaseTransport):
    """Connects to the IP validated for each host (resolved once per transport)."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None, *, allow_private: bool = False) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)
        self.allow_private = allow_private
        self.pins: dict[tuple[str, int], str] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host, port = check_shape(str(url))
        key = (host, port)
        if key not in self.pins:
            self.pins[key] = await resolve_validated(host, port, self.allow_private)
        ip = self.pins[key]
        if ip != host.strip("[]"):
            default = port == (443 if url.scheme == "https" else 80)
            request.headers["Host"] = host if default else f"{host}:{port}"
            request.url = url.copy_with(host=ip)
            if url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": host}
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def pinned_client(
    *, timeout_s: float = 30.0, allow_private: bool = False, follow_redirects: bool = False
) -> httpx.AsyncClient:
    """An `AsyncClient` whose every connection (redirects included) is resolve+validate+pin."""
    return httpx.AsyncClient(
        transport=PinnedTransport(allow_private=allow_private),
        timeout=timeout_s,
        follow_redirects=follow_redirects,
        trust_env=False,
    )


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool


async def safe_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    timeout_s: float = 30.0,
    allow_private: bool = False,
) -> Fetched:
    """Request with manual redirects; every hop is shape-checked and connects to a pinned,
    validated address (see module docstring)."""
    async with pinned_client(timeout_s=timeout_s, allow_private=allow_private) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_shape(current)
            async with client.stream(method, current, headers=headers, content=content) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    method, content = (
                        ("GET", None) if resp.status_code in {301, 302, 303} else (method, content)
                    )
                    continue
                buf = bytearray()
                truncated = False
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        truncated = True
                        del buf[max_bytes:]
                        break
                return Fetched(
                    current,
                    resp.status_code,
                    resp.headers.get("content-type", ""),
                    bytes(buf),
                    truncated,
                )
        raise ToolError(f"too many redirects (>{MAX_REDIRECTS})")
