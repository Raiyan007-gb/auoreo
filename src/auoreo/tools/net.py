"""SSRF-safe HTTP helpers shared by network tools (web_fetch, http_request)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from auoreo.tools.context import ToolError

MAX_REDIRECTS = 5


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def check_url(url: str, allow_private: bool = False) -> None:
    """Reject non-http(s) URLs and hosts resolving to private/loopback/link-local IPs."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ToolError(f"only http/https URLs are allowed (got '{parts.scheme}')")
    if not parts.hostname:
        raise ToolError("URL has no host")
    if parts.username or parts.password:
        raise ToolError("credentials in URLs are not allowed")
    if allow_private:
        return
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve host '{parts.hostname}': {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _blocked(ip):
            raise ToolError(f"blocked address {ip} for host '{parts.hostname}' (SSRF guard)")


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
    """Request with manual redirects; every hop is re-validated against the SSRF guard."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(current, allow_private)
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
