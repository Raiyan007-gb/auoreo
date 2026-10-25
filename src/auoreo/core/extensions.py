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
    "auoreo.runtime.hooks:start",
    "auoreo.automation.deliveries:start",
    "auoreo.automation.scheduler:start",
    "auoreo.automation.webhooks:start",
    "auoreo.teams:start",  # key = last dotted part → services.ext["teams"]
    "auoreo.plugins:start",  # → services.ext["plugins"]
    "auoreo.voice:start",  # M4c → services.ext["voice"]
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
