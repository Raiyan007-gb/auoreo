"""`allowed_cidrs: [github]` preset (spec §19.11, §24.8): GitHub's webhook source ranges.

`GET https://api.github.com/meta` → `hooks: [CIDR, …]`, cached at
`<home>/cache/github-meta.json` as `{"fetched_at": <unix>, "hooks": [...]}`. Refreshed at most
every `i6.webhooks.github_meta_refresh_s` (daily): a stale cache is served immediately and
refreshed in the background, so a webhook request never waits on GitHub. Offline-safe: a
failed refresh keeps the old ranges (doctor warns once they are older than
`github_meta_stale_days`, 7). With no cache at all the first request waits for one fetch; if
that fails the preset expands to nothing, so the route fails closed (403).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

log = logging.getLogger(__name__)
PRESET = "github"
FETCH_TIMEOUT_S = 5.0
_refreshing: dict[Path, asyncio.Task[Any]] = {}


def cache_path(home: Path) -> Path:
    return home / "cache" / "github-meta.json"


def read_cache(home: Path) -> tuple[float, list[str]] | None:
    try:
        data = json.loads(cache_path(home).read_text(encoding="utf-8"))
        hooks = [str(c) for c in data["hooks"]]
        return float(data["fetched_at"]), hooks
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _valid(cidrs: list[Any]) -> list[str]:
    out = []
    for c in cidrs:
        try:
            out.append(str(ipaddress.ip_network(str(c), strict=False)))
        except ValueError:
            continue
    return out


async def fetch(settings: Settings, client: httpx.AsyncClient | None = None) -> list[str] | None:
    """One `GET /meta`; writes the cache and returns the hook ranges (None when it failed)."""
    url = settings.i6.webhooks.github_meta_url
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as c:
                resp = await c.get(url, headers={"Accept": "application/vnd.github+json"})
        else:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        hooks = _valid(list(resp.json().get("hooks") or []))
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning("github /meta refresh failed (%s); keeping the cached ranges", type(exc).__name__)
        return None
    if not hooks:
        return None
    path = cache_path(settings.home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched_at": time.time(), "hooks": hooks}), encoding="utf-8")
    tmp.replace(path)
    return hooks


def _refresh_later(settings: Settings) -> None:
    key = settings.home
    task = _refreshing.get(key)
    if task is not None and not task.done():
        return
    _refreshing[key] = asyncio.get_running_loop().create_task(fetch(settings), name="github-meta")


async def hook_cidrs(settings: Settings) -> list[str]:
    """Current GitHub hook ranges (see the module doc for the cache/refresh rules)."""
    cached = read_cache(settings.home)
    if cached is None:
        return await fetch(settings) or []
    fetched_at, hooks = cached
    if time.time() - fetched_at >= settings.i6.webhooks.github_meta_refresh_s:
        _refresh_later(settings)
    return hooks


async def expand(cidrs: list[str], settings: Settings) -> list[str]:
    """`allowed_cidrs` with the `github` preset replaced by GitHub's hook ranges."""
    if PRESET not in cidrs:
        return cidrs
    return [c for c in cidrs if c != PRESET] + await hook_cidrs(settings)


def doctor_row(settings: Settings, routes: list[str]) -> tuple[str, str, str, str] | None:
    """(check, level, detail, remedy) when some route uses the preset; None otherwise."""
    if not routes:
        return None
    cached = read_cache(settings.home)
    remedy = "a webhook request refreshes it; check outbound access to api.github.com"
    if cached is None:
        return ("webhook:github-meta", "warn", f"no cached GitHub hook ranges ({', '.join(routes)})", remedy)
    age_d = (time.time() - cached[0]) / 86_400
    if age_d > settings.i6.webhooks.github_meta_stale_days:
        return ("webhook:github-meta", "warn", f"GitHub hook ranges are {age_d:.0f} days old", remedy)
    return ("webhook:github-meta", "ok", f"{len(cached[1])} GitHub hook range(s), {age_d:.1f} days old", "")


__all__ = ["PRESET", "cache_path", "doctor_row", "expand", "fetch", "hook_cidrs", "read_cache"]
