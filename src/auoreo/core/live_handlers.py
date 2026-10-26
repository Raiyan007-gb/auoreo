"""Per-area refresh paths for `core.live_settings.apply` (dict dispatch, no elif chains).

Every handler runs AFTER `svc.settings` was updated in place, so it reads the new values from
`svc.settings`. Services that read settings lazily (most of them: the runner, executor, teams,
voice, suggestions, egress proxy, …) need nothing here; these are the ones that cache.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)
Handler = Callable[[Any, set[str]], Awaitable[None]]

GATEWAY_KEYS = frozenset(
    {"model", "small_model", "models", "aliases", "fallbacks", "auxiliary", "gateway_tuning", "i2"}
)
AREAS: dict[str, str] = {
    **dict.fromkeys(GATEWAY_KEYS, "gateway"),
    "strict_overlays": "harness",
    "tool_engine": "harness",
    "plugins_enabled": "plugins",
    "plugins": "plugins",
    "scheduler_enabled": "scheduler",
    "webhooks_enabled": "deliveries",
    "hooks_enabled": "hooks",
    "voice_enabled": "voice",
    "sentry_dsn": "observability",
    "sentry_share": "observability",
    "trace_file": "observability",
    "otel_capture_content": "observability",
    "rerank_model": "backends",
    "rerank_download_models": "backends",
    "detection_model": "backends",
    "voice_stt_model": "backends",
    "voice_tts_voice": "backends",
    "env": "env",
}
"""Top-level settings field → refresh area. Fields not listed are read lazily (live already)."""
BACKEND_KINDS: dict[str, tuple[str, ...]] = {
    "rerank_model": ("rerank",),
    "rerank_download_models": ("rerank",),
    "detection_model": ("detection",),
    "voice_stt_model": ("stt",),
    "voice_tts_voice": ("tts",),
}
"""Cached backend instances rebuilt (next use) when one of these fields changes."""


async def _gateway(svc: Any, keys: set[str]) -> None:
    gw = svc.gateway
    if "i2" in keys and getattr(gw, "budgets", None) is not None:
        from auoreo.gateway.budgets import BudgetTracker

        gw.budgets = BudgetTracker(svc.settings.i2.budgets)
    if hasattr(gw, "variants"):
        gw.variants = None  # prompts/variants.yaml is re-matched against the new deployments
    reload = getattr(gw, "reload_credentials", None)
    if reload is not None:
        reload()  # rebuilds the model list + Router from svc.settings and the secret store


async def _harness(svc: Any, keys: set[str]) -> None:
    from auoreo.harness.registry import HarnessError

    h = svc.harness
    h.strict_overlays = svc.settings.strict_overlays
    h.error_on_collision = svc.settings.tool_engine.error_on_collision
    if getattr(h, "availability", None) is not None:
        h.availability.ttl = svc.settings.tool_engine.check_fn_ttl_s
    try:
        h.reload()
    except HarnessError as exc:  # the previous snapshot stays; the diff reports it
        log.warning("harness reload after a settings change failed: %s", exc)


async def _plugins(svc: Any, keys: set[str]) -> None:
    host = svc.ext.get("plugins")
    if host is None:
        return
    await host.sync()
    if "plugins" not in keys:
        return
    for name, st in list(getattr(host, "plugins", {}).items()):
        if st.status not in {"disabled", "stopped"} and host.config_for(name) != st.config:
            await host.reload_plugin(name)


async def _scheduler(svc: Any, keys: set[str]) -> None:
    sched = svc.ext.get("scheduler")
    if sched is not None:
        if svc.settings.scheduler_enabled and not sched.active:
            await sched.start()
        elif not svc.settings.scheduler_enabled and sched.active:
            await sched.close()
    await _deliveries(svc, keys)


async def _deliveries(svc: Any, keys: set[str]) -> None:
    on = svc.settings.webhooks_enabled or svc.settings.scheduler_enabled
    await toggle(svc, "deliveries", "auoreo.automation.deliveries:start", on)


async def _hooks(svc: Any, keys: set[str]) -> None:
    await toggle(svc, "hooks", "auoreo.runtime.hooks:start", svc.settings.hooks_enabled)


async def _voice(svc: Any, keys: set[str]) -> None:
    await toggle(svc, "voice", "auoreo.voice:start", svc.settings.voice_enabled)


async def toggle(svc: Any, key: str, ref: str, on: bool) -> None:
    """Start an extension that is now enabled / close one that is now disabled."""
    present = key in svc.ext
    if on and not present:
        mod, fn = ref.split(":")
        obj = await getattr(importlib.import_module(mod), fn)(svc)
        if obj is not None:
            svc.ext[key] = obj
    elif not on and present:
        obj = svc.ext.pop(key)
        if (close := getattr(obj, "close", None)) is not None:
            res = close()
            if hasattr(res, "__await__"):
                await res


async def _observability(svc: Any, keys: set[str]) -> None:
    from auoreo.core import observability as obs

    obs._side_setup(svc.settings)  # trace file + Sentry follow the settings (both idempotent)
    obs._state.capture_content = bool(svc.settings.otel_capture_content)


async def _backends(svc: Any, keys: set[str]) -> None:
    kinds = {k for key in keys for k in BACKEND_KINDS.get(key, ())}
    cached = getattr(svc, "_backends", {})
    for kind in kinds & set(cached):
        inst = cached.pop(kind)
        if (close := getattr(inst, "close", None)) is not None:
            try:
                res = close()
                if hasattr(res, "__await__"):
                    await res
            except Exception:  # a backend that fails to close is dropped anyway
                log.exception("backend %s failed to close after a settings change", kind)


async def _env(svc: Any, keys: set[str]) -> None:
    from auoreo.core.env_custom import refresh_redaction

    refresh_redaction(svc)


HANDLERS: dict[str, Handler] = {
    "gateway": _gateway,
    "harness": _harness,
    "plugins": _plugins,
    "scheduler": _scheduler,
    "deliveries": _deliveries,
    "hooks": _hooks,
    "voice": _voice,
    "observability": _observability,
    "backends": _backends,
    "env": _env,
}


async def run(svc: Any, changed: list[str]) -> list[str]:
    """Run each touched area's handler once; returns the areas that failed (logged)."""
    areas: dict[str, set[str]] = {}
    for key in changed:
        if (area := AREAS.get(key)) is not None:
            areas.setdefault(area, set()).add(key)
    failed: list[str] = []
    for area, keys in areas.items():
        try:
            await HANDLERS[area](svc, keys)
        except Exception:  # one area must not stop the others from refreshing
            log.exception("live settings: %s refresh failed", area)
            failed.append(area)
    return failed


__all__ = ["AREAS", "GATEWAY_KEYS", "HANDLERS", "run", "toggle"]
