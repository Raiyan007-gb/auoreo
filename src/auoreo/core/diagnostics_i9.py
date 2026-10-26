"""I9 doctor rows (spec §25.10 L1017, §25.12 L1037/L1044, §25.15 L1097). Never repairs anything.

- `egress-proxy`: the loopback proxy can be started and refuses an unauthenticated tunnel (407)
  and the cloud metadata address (403);
- `egress-sandbox`: how `net: proxy` children are confined here (Landlock CONNECT_TCP to the
  proxy port on ABI >= 4, else an honest "not enforced" warning);
- `ws-server`: the pinned uvicorn WebSocket implementation is importable; resolved protocol
  pings for this bind and the app-level ping cadence (must be ≤ 30 s);
- `reverse-proxy`: with `i9.reverse_proxy_url`, `/api/events` is fetched through the proxy and
  must answer `no-cache`, not be buffered, and send a first frame within 30 s;
- `search`: which providers of the `web_search` chain have their key/URL (names only).
"""

from __future__ import annotations

import asyncio
import base64
import os
import time

import httpx

from auoreo.core.diagnostics import Check, Level
from auoreo.core.settings import Settings

PROBE_TIMEOUT_S = 5.0
SSE_FIRST_FRAME_S = 30.0


async def _raw(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
        return await asyncio.wait_for(reader.read(4096), PROBE_TIMEOUT_S)
    finally:
        writer.close()


async def egress_rows(st: Settings) -> list[Check]:
    from auoreo.tools.egress import EgressProxy

    if not st.i9.egress.enabled:
        return [
            Check(
                "egress-proxy",
                "warn",
                "disabled: net: proxy tools use the pinned direct client",
                "set i9.egress.enabled = true",
            )
        ]
    probe = EgressProxy(st)
    try:
        await probe.start()
        anon = await _raw(probe.port, b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        token = probe.mint("doctor")
        auth = base64.b64encode(f"auoreo:{token}".encode()).decode()
        meta = await _raw(
            probe.port,
            f"CONNECT 169.254.169.254:80 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode(),
        )
    except (OSError, TimeoutError) as exc:
        return [
            Check("egress-proxy", "error", f"cannot run the loopback proxy: {exc}", "check loopback sockets")
        ]
    finally:
        await probe.close()
    ok = anon.startswith(b"HTTP/1.1 407") and meta.startswith(b"HTTP/1.1 403")
    cfg = st.i9.egress
    lists = f"allow={len(cfg.allow_hosts)} deny={len(cfg.deny_hosts)} private={len(cfg.allow_private_hosts)}"
    if ok:
        return [
            Check(
                "egress-proxy", "ok", f"loopback CONNECT proxy: 407 without token, 403 for metadata ({lists})"
            )
        ]
    return [
        Check("egress-proxy", "error", "proxy did not refuse the probes (407/403 expected)", "report a bug")
    ]


def sandbox_row(st: Settings) -> Check:
    from auoreo.backends import landlock
    from auoreo.backends.sandbox import proxy_confinement

    abi = landlock.abi()
    selected = (st.backends or {}).get("sandbox", "auto")
    how = proxy_confinement("proxy", 1, abi).replace(" 1", "")
    level: Level = "ok" if abi >= 4 else "warn"
    fix = (
        ""
        if abi >= 4
        else "kernel >= 6.7 with Landlock (ABI 4) confines net: proxy children; docker gets no network"
    )
    return Check("egress-sandbox", level, f"backend {selected}: {how}", fix)


def ws_row(st: Settings) -> Check:
    from auoreo.core.ws_server import APP_PING_MAX_S, impl_available, ws_kwargs

    kw = ws_kwargs(st)
    ok, detail = impl_available(st.i9.ws.impl)
    ping = (
        "off (loopback; app ping authoritative)"
        if kw["ws_ping_interval"] is None
        else (f"{kw['ws_ping_interval']:g}s/{kw['ws_ping_timeout']:g}s")
    )
    app = st.ws_ping_interval_s
    if not ok:
        return Check("ws-server", "error", f"{st.i9.ws.impl}: {detail}", "uv sync (uvicorn[standard])")
    if app > APP_PING_MAX_S:
        return Check(
            "ws-server", "warn", f"app ping {app:g}s > {APP_PING_MAX_S:g}s", "set ws_ping_interval_s <= 30"
        )
    return Check("ws-server", "ok", f"ws={st.i9.ws.impl}; protocol ping {ping}; app ping {app:g}s")


async def reverse_proxy_rows(st: Settings) -> list[Check]:
    url = st.i9.reverse_proxy_url
    if not url or os.environ.get("AUOREO_OFFLINE"):
        return []
    headers = {"Accept": "text/event-stream"}
    if st.server_password:
        headers["Authorization"] = f"Bearer {st.server_password}"
    start = time.monotonic()
    try:
        timeout = httpx.Timeout(10, read=SSE_FIRST_FRAME_S)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("GET", url.rstrip("/") + "/api/events", headers=headers) as res,
        ):
            cache = res.headers.get("cache-control", "")
            buffering = res.headers.get("x-accel-buffering", "")
            first = await anext(res.aiter_bytes(), b"")
    except (httpx.HTTPError, StopAsyncIteration) as exc:
        return [
            Check(
                "reverse-proxy",
                "error",
                f"{url}: {type(exc).__name__}: {exc}"[:300],
                "see deploy/nginx/auoreo.conf (SSE: proxy_buffering off, read timeout 3600)",
            )
        ]
    took = time.monotonic() - start
    problems = []
    if res.status_code != 200:
        problems.append(f"HTTP {res.status_code}")
    if "no-cache" not in cache:
        problems.append(f"cache-control {cache!r} lacks no-cache")
    if buffering.lower() == "yes":
        problems.append("x-accel-buffering: yes")
    if not first or took > SSE_FIRST_FRAME_S:
        problems.append(f"no SSE frame within {SSE_FIRST_FRAME_S:g}s")
    if problems:
        return [
            Check("reverse-proxy", "error", f"{url}: " + "; ".join(problems), "see deploy/nginx/auoreo.conf")
        ]
    return [Check("reverse-proxy", "ok", f"{url}: /api/events streams (no-cache, first frame {took:.1f}s)")]


def search_row(st: Settings) -> Check:
    have = []
    keys = {
        "exa": "EXA_API_KEY",
        "parallel": "PARALLEL_API_KEY",
        "brave": "BRAVE_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "jina": "JINA_API_KEY",
        "firecrawl": "FIRECRAWL_API_KEY",
    }
    for name in st.i9.search.order:
        if name == "searxng" and st.searxng_url or name in keys and os.environ.get(keys[name]):
            have.append(name)
    order = " > ".join(st.i9.search.order)
    if have:
        return Check("search", "ok", f"web_search chain {order}; keys in env for: {', '.join(have)}")
    return Check(
        "search", "ok", f"web_search chain {order}; no key in env (the secret store is read per call)"
    )


async def i9_checks(st: Settings) -> list[Check]:
    return [
        *await egress_rows(st),
        sandbox_row(st),
        ws_row(st),
        *await reverse_proxy_rows(st),
        search_row(st),
    ]
