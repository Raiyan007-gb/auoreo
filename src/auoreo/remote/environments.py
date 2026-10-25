"""Multi-environment listing (`GET /api/environments`, `auoreo env list`): discovered
`environment` backends, named environments (`settings.environments`, when present), the
configured exec-server and the bridge peers — each with a `check()` status, never secrets."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from auoreo.backends import BackendStatus
from auoreo.schema.bridge import EnvironmentInfo

if TYPE_CHECKING:
    from auoreo.core.services import Services

KIND = "environment"
CHECK_TIMEOUT_S = 5.0


def public_url(url: str) -> str:
    """Drop userinfo, query and fragment (where tokens like to hide)."""
    if url.startswith("unix://"):
        return url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


async def _checked(aw: Awaitable[BackendStatus]) -> tuple[bool, str]:
    try:
        st = await asyncio.wait_for(aw, CHECK_TIMEOUT_S)
    except TimeoutError:
        return False, "check timed out"
    except Exception as exc:  # a broken backend is a status, not a 500
        return False, f"{type(exc).__name__}: {exc}"
    return st.ok, st.detail


def _selected(svc: Services) -> tuple[str | None, str]:
    try:
        bdef = svc.harness.select_backend(KIND, svc.settings.backends.get(KIND))
    except KeyError as exc:
        return None, str(exc).strip("'\"")
    return (bdef.manifest.name if bdef else None), ""


async def _backends(svc: Services) -> list[EnvironmentInfo]:
    selected, problem = _selected(svc)
    out: list[EnvironmentInfo] = []
    for name, bdef in sorted(svc.harness.backends_for(KIND).items()):
        try:
            remote = bool(getattr(bdef.load(), "remote", False))
        except Exception as exc:
            out.append(
                EnvironmentInfo(name=name, kind="backend", backend=bdef.name, ok=False, detail=str(exc))
            )
            continue
        info = EnvironmentInfo(
            name=name, kind="backend", backend=bdef.name, remote=remote, selected=name == selected
        )
        if name == selected:
            info.ok, info.detail = await _checked(svc.backend(KIND).check())
        else:
            info.detail = problem or bdef.manifest.description.split(".")[0]
        out.append(info)
    return out


def _named(svc: Services) -> list[EnvironmentInfo]:
    named = getattr(svc.settings, "environments", None) or {}
    out: list[EnvironmentInfo] = []
    for name, cfg in dict(named).items():
        get = cfg.get if isinstance(cfg, dict) else lambda k, c=cfg: getattr(c, k, None)
        backend = get("backend")
        out.append(
            EnvironmentInfo(
                name=str(name),
                kind="named",
                backend=f"{KIND}/{backend}" if backend else None,
                remote=bool(get("remote")),
                detail="configured (checked when used)",
            )
        )
    return out


async def _exec_server(svc: Services) -> list[EnvironmentInfo]:
    cfg = svc.settings.environment_remote
    if cfg is None:
        return []
    info = EnvironmentInfo(
        name="remote", kind="remote", backend=f"{KIND}/remote", remote=True, url=public_url(cfg.url)
    )
    bdef = svc.harness.backends_for(KIND).get("remote")
    if bdef is None:
        info.ok, info.detail = False, "backends/environment/remote is not installed"
        return [info]
    selected, _ = _selected(svc)
    inst: Any = svc.backend(KIND) if selected == "remote" else bdef.load()(svc.settings)
    try:
        info.ok, info.detail = await _checked(inst.check())
    finally:
        if selected != "remote":
            await inst.close()
    return [info]


async def _bridge(name: str, url: str) -> EnvironmentInfo:
    info = EnvironmentInfo(
        name=name, kind="bridge", remote=not url.startswith("unix://"), url=public_url(url)
    )
    transport: httpx.AsyncBaseTransport | None = None
    base = url.rstrip("/")
    if url.startswith("unix://"):
        transport, base = httpx.AsyncHTTPTransport(uds=url.removeprefix("unix://")), "http://auoreo"
    try:
        async with httpx.AsyncClient(transport=transport, timeout=CHECK_TIMEOUT_S) as c:
            r = await c.get(base + "/api/health")
        info.ok = r.status_code == 200
        info.detail = f"auoreo {r.json().get('version')}" if info.ok else f"HTTP {r.status_code}"
    except (httpx.HTTPError, OSError, ValueError) as exc:
        info.ok, info.detail = False, f"unreachable: {type(exc).__name__}"
    return info


async def list_environments(svc: Services) -> list[EnvironmentInfo]:
    bridges = await asyncio.gather(*(_bridge(n, p.url) for n, p in sorted(svc.settings.bridges.items())))
    return [*await _backends(svc), *_named(svc), *await _exec_server(svc), *bridges]
