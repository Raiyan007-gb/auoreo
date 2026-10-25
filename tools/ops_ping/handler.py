"""Reachability + latency: ICMP via `ping3` (extra `ops`, imported lazily) when it is
installed and the process may open an ICMP socket; otherwise a TCP connect probe. A
refused connection still proves the host is up (it answered with RST)."""

import asyncio
import contextlib
import socket
import time
from typing import Any

from auoreo.tools.context import ToolContext, ToolError, ToolResult

DEFAULT_PORTS = [443, 80, 22]


class IcmpUnavailable(Exception):
    pass


def _icmp_once(ip: str, timeout: float) -> float | None:
    """Seconds, or None on timeout. Raises IcmpUnavailable when ICMP cannot be used here."""
    try:
        import ping3
    except ImportError as exc:
        raise IcmpUnavailable("ping3 is not installed (extra `ops`)") from exc
    try:
        delay = ping3.ping(ip, timeout=timeout, unit="s")
    except OSError as exc:  # PermissionError: no raw/unprivileged ICMP socket allowed
        raise IcmpUnavailable(f"ICMP not permitted ({exc})") from exc
    if delay is False:
        raise IcmpUnavailable("ICMP probe failed")
    return float(delay) if delay is not None else None


async def _tcp_once(ip: str, port: int, timeout: float) -> tuple[float | None, str]:
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except ConnectionRefusedError:
        return time.perf_counter() - start, "refused"
    except (TimeoutError, OSError):
        return None, "no answer"
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return time.perf_counter() - start, "open"


async def _resolve(host: str) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve host '{host}': {exc}") from exc
    return str(infos[0][4][0])


async def _icmp(ip: str, count: int, timeout: float) -> list[float | None]:
    out: list[float | None] = []
    for _ in range(count):
        out.append(await asyncio.to_thread(_icmp_once, ip, timeout))
    return out


async def _tcp(ip: str, ports: list[int], count: int, timeout: float) -> tuple[int, list[float | None], str]:
    """Pick the first port that answers (open or refused), then probe it `count` times."""
    for port in ports:
        first, state = await _tcp_once(ip, port, timeout)
        if first is None:
            continue
        rtts: list[float | None] = [first]
        for _ in range(count - 1):
            rtts.append((await _tcp_once(ip, port, timeout))[0])
        return port, rtts, state
    return ports[0], [None] * count, "no answer"


def _summary(rtts: list[float | None]) -> tuple[str, dict[str, Any]]:
    ok = [r * 1000 for r in rtts if r is not None]
    meta: dict[str, Any] = {"sent": len(rtts), "received": len(ok)}
    if not ok:
        return f"0/{len(rtts)} replies", meta
    meta.update(min_ms=round(min(ok), 2), avg_ms=round(sum(ok) / len(ok), 2), max_ms=round(max(ok), 2))
    return (
        f"{len(ok)}/{len(rtts)} replies, rtt min/avg/max {meta['min_ms']:.2f}/{meta['avg_ms']:.2f}/"
        f"{meta['max_ms']:.2f} ms",
        meta,
    )


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    host, count = str(args["host"]), int(args.get("count", 3))
    timeout = float(args.get("timeout_s", 2))
    method = str(args.get("method", "auto"))
    if args.get("port") and method == "auto":
        method = "tcp"
    ip = await _resolve(host)
    note = ""
    if method in {"auto", "icmp"}:
        try:
            rtts = await _icmp(ip, count, timeout)
            text, meta = _summary(rtts)
            return ToolResult(
                f"**Ping** {host} ({ip}) via ICMP: {text}",
                meta={"host": host, "ip": ip, "method": "icmp", "reachable": meta["received"] > 0, **meta},
            )
        except IcmpUnavailable as exc:
            if method == "icmp":
                raise ToolError(f"ICMP unavailable: {exc}; retry with method tcp") from exc
            note = f" (ICMP unavailable: {exc})"
    svc = ctx.services.get("services")
    cfg_ports = getattr(getattr(getattr(svc, "settings", None), "tools", None), "ping_tcp_ports", None)
    ports = [int(args["port"])] if args.get("port") else list(cfg_ports or DEFAULT_PORTS)
    port, rtts, state = await _tcp(ip, ports, count, timeout)
    text, meta = _summary(rtts)
    detail = (
        f"port {port} {state}" if meta["received"] else f"no answer on port(s) {', '.join(map(str, ports))}"
    )
    return ToolResult(
        f"**Ping** {host} ({ip}) via TCP connect{note}: {text} — {detail}",
        meta={
            "host": host,
            "ip": ip,
            "method": "tcp",
            "port": port,
            "reachable": meta["received"] > 0,
            **meta,
        },
    )
