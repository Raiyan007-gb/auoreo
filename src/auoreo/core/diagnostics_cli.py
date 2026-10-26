"""G5a doctor rows that only make sense from the CLI (spec §18/§20.3): is the server on our
port running *this* code (`code_sha`), does `/ws` upgrade, tunnels, SearXNG, `gh auth`,
the `websockets` import and the update manifest.

Every row is honest: a probe that does not apply (no server running, nothing configured,
`AUOREO_OFFLINE` set, tool not installed) reports `ok` with "skipped: <why>" instead of
pretending to have checked. Network probes use short timeouts and never send secrets
anywhere but our own server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

import httpx

from auoreo.core.diagnostics import Check, Level
from auoreo.core.diagnostics_i5 import sse_row  # --- I5
from auoreo.core.redaction import redact
from auoreo.core.settings import Settings

TIMEOUT_S = 3.0
WS_TIMEOUT_S = 5.0


def _offline() -> bool:
    return bool(os.environ.get("AUOREO_OFFLINE"))


def server_url(st: Settings) -> str:
    host = "127.0.0.1" if st.host in {"0.0.0.0", "::", ""} else st.host
    return f"http://{f'[{host}]' if ':' in host else host}:{st.port}"


def _auth(st: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {st.server_password}"} if st.server_password else {}


async def live_health(st: Settings, base: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.get(base + "/api/health")
        body = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("status") == "ok" else None


def code_sha_row(health: dict[str, Any] | None, base: str) -> Check:
    from auoreo.core.buildinfo import code_sha

    if health is None:
        return Check("code_sha", "ok", f"skipped: no auoreo server answering on {base}")
    live, mine = health.get("code_sha"), code_sha()
    if not live:
        return Check(
            "code_sha",
            "warn",
            f"server on {base} reports no code_sha (older build)",
            "restart `auoreo serve`",
        )
    if live != mine:
        return Check(
            "code_sha",
            "warn",
            f"server on {base} runs {live}, this install is {mine}",
            "restart the server so it runs the code you have now",
        )
    return Check("code_sha", "ok", f"server on {base} runs this code ({mine})")


def websockets_row() -> Check:
    try:
        import websockets
    except ImportError:
        return Check(
            "websockets", "warn", "not importable (/ws needs it)", "uv sync (websockets is a core dep)"
        )
    return Check("websockets", "ok", f"websockets {getattr(websockets, '__version__', '?')}")


async def ws_upgrade_row(st: Settings, health: dict[str, Any] | None, base: str) -> Check:
    if health is None:
        return Check("ws_upgrade", "ok", "skipped: no running server to upgrade against")
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        return Check("ws_upgrade", "warn", "skipped: websockets not importable")
    url = base.replace("http", "ws", 1) + "/ws"
    try:
        async with asyncio.timeout(WS_TIMEOUT_S):
            async with connect(url, additional_headers=_auth(st), open_timeout=WS_TIMEOUT_S) as ws:
                first = json.loads(await ws.recv())
    except Exception as exc:
        detail = redact(f"{type(exc).__name__}: {exc}")[:200]
        fix = "check --no-ws / --ws-auth, the password, or a proxy stripping Upgrade headers"
        return Check("ws_upgrade", "warn", f"{url} upgrade failed: {detail}", fix)
    return Check("ws_upgrade", "ok", f"{url} upgraded (first frame: {first.get('type', '?')})")


def tunnel_row(st: Settings) -> Check:
    from auoreo.core.tunnels import TunnelManager, pid_alive

    info = TunnelManager(st.home, st.server_password).status()
    have = [b for b in ("cloudflared", "tailscale") if shutil.which(b)]
    tools = f"; installed: {', '.join(have)}" if have else "; no tunnel binary installed"
    if info is None:
        return Check("tunnel", "ok", f"none running{tools}")
    if info.pid is not None and not pid_alive(info.pid):
        return Check("tunnel", "warn", f"stale {info.provider} record ({info.url})", "auoreo tunnel stop")
    if not st.server_password:
        return Check(
            "tunnel", "error", f"{info.provider} tunnel at {info.url} without a password", "set a password"
        )
    return Check("tunnel", "ok", f"{info.provider} → {info.url} (port {info.port}){tools}")


async def searxng_row(st: Settings) -> Check:
    url = st.searxng_url
    if not url:
        return Check("searxng", "ok", "skipped: searxng_url not configured")
    if _offline():
        return Check("searxng", "ok", f"skipped (AUOREO_OFFLINE): {url}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.get(url.rstrip("/") + "/search", params={"q": "auoreo", "format": "json"})
    except httpx.HTTPError as exc:
        return Check(
            "searxng", "warn", f"{url} unreachable: {type(exc).__name__}", "start SearXNG or fix searxng_url"
        )
    if r.status_code == 403:
        return Check(
            "searxng", "warn", f"{url} refuses JSON (403)", "enable `json` in SearXNG settings.yml formats"
        )
    level: Level = "ok" if r.status_code == 200 else "warn"
    return Check("searxng", level, f"{url} answered {r.status_code}", "check the SearXNG instance")


def gh_row() -> Check:
    gh = shutil.which("gh")
    if gh is None:
        return Check("gh_auth", "ok", "skipped: gh not installed (GitHub tools need it)")
    try:
        proc = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(
            "gh_auth", "warn", f"gh auth status failed: {type(exc).__name__}", "run `gh auth status`"
        )
    lines = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    summary = redact(
        next(
            (ln for ln in lines if "Logged in" in ln or "not logged" in ln.lower()), lines[0] if lines else ""
        )
    )
    if proc.returncode != 0:
        return Check("gh_auth", "warn", f"not authenticated: {summary[:160]}", "gh auth login")
    return Check("gh_auth", "ok", summary[:160] or "authenticated")


async def update_row(st: Settings) -> Check:
    url = st.update_url.format(channel=st.update_channel)
    if _offline():
        return Check("update", "ok", f"skipped (AUOREO_OFFLINE): channel {st.update_channel}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        return Check(
            "update", "warn", f"channel {st.update_channel}: {url} unreachable ({type(exc).__name__})"
        )
    if r.status_code != 200:
        return Check(
            "update",
            "warn",
            f"channel {st.update_channel}: manifest answered {r.status_code}",
            "check update_url",
        )
    return Check("update", "ok", f"channel {st.update_channel}: manifest reachable ({len(r.content)} bytes)")


async def cli_checks(st: Settings) -> list[Check]:
    """The G5a rows, in display order. A probe that raises becomes one `warn` row."""
    base = server_url(st)
    health = await live_health(st, base)
    rows: list[Check] = [code_sha_row(health, base), websockets_row()]
    probes: list[tuple[str, Any]] = [
        ("ws_upgrade", ws_upgrade_row(st, health, base)),
        ("tunnel", asyncio.to_thread(tunnel_row, st)),
        ("searxng", searxng_row(st)),
        ("gh_auth", asyncio.to_thread(gh_row)),
        ("update", update_row(st)),
        ("sse_connections", sse_row(st, base, health)),  # --- I5
    ]
    results = await asyncio.gather(*(p for _, p in probes), return_exceptions=True)
    for (name, _), res in zip(probes, results, strict=True):
        rows.append(res if isinstance(res, Check) else Check(name, "warn", f"check failed: {res}"))
    return rows
