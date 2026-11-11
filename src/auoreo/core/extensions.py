"""Extension starters: optional subsystems (hooks, scheduler, webhooks, teams, plugins)
that attach to `Services` at startup without `services.py` knowing each one.

Each entry is `"module:function"`; the function is `async def start(services) -> object | None`
and may expose `async def close()` on the returned object. Add one line per subsystem.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

STARTERS: list[str] = [
    "auoreo.core.sandbox_boot:start",  # first: policy=fail refuses before anything else starts
    "auoreo.tools.egress:start",  # I9 → services.ext["egress"] (loopback CONNECT proxy for net: proxy)
    "auoreo.core.secrets:start",  # G1b: audit scoped secret reads (secret.read{name, by})
    "auoreo.core.live_settings:start",  # ENV → ext["live_settings"]: live settings/secrets apply + watcher
    "auoreo.gateway.i2_boot:start",  # I2: rotation grace, key pools, budgets, OR catalog
    "auoreo.runtime.hooks:start",
    "auoreo.automation.deliveries:start",
    "auoreo.automation.scheduler:start",
    "auoreo.automation.webhooks:start",
    "auoreo.teams:start",  # key = last dotted part → services.ext["teams"]
    "auoreo.teams.dispatcher:start",  # I3 → services.ext["dispatcher"] (off unless i3.kanban.dispatch)
    "auoreo.teams.worktree_reconcile:start",  # M7 W4 Wave 2: gc journal → worktree.removed|gc events
    "auoreo.plugins:start",  # → services.ext["plugins"]
    "auoreo.memory.curator:start",  # → services.ext["curator"] (deterministic, no LLM)
    "auoreo.memory.reembed:start",  # I4 → services.ext["reembed"] (resumable re-embed job)
    "auoreo.memory.tenants:start",  # I4 → services.ext["tenants"] (team.created → memory tenant)
    "auoreo.voice:start",  # M4c → services.ext["voice"]
    "auoreo.goals:start",  # W0-C → services.ext["goals"]
    "auoreo.suggestions:start",  # W0-C → services.ext["suggestions"] (60 s expiry/delivery sweep)
    "auoreo.core.bus_redis:start",  # W1-E → services.ext["bus_redis"]; no-op unless redis_url is set
    "auoreo.core.i8_boot:start",  # I8 → ext["i8_boot"]: CU turn-end unhide, wake daemon, LiveKit worker
]


async def start_all(services: Services) -> dict[str, Any]:
    started: dict[str, Any] = {}
    for ref in STARTERS:
        mod_name, fn_name = ref.split(":")
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name)
            obj = await fn(services)
        except Exception:  # one broken extension must not stop the server
            log.exception("extension %s failed to start", ref)
            continue
        if obj is not None:
            started[mod_name.rsplit(".", 1)[-1]] = obj
    return started


async def close_all(started: dict[str, Any]) -> None:
    for name, obj in reversed(list(started.items())):
        close = getattr(obj, "close", None)
        if close is None:
            continue
        try:
            res = close()
            if hasattr(res, "__await__"):
                await res
        except Exception:
            log.exception("extension %s failed to close", name)
