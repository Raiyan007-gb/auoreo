"""I2 extension starter: wire the LiteLLM gateway to the secret store (rotation grace,
key pools, hot reload), the audit log, today's ledger spend (budgets) and, when enabled, the
OpenRouter live catalog. A fake gateway (no `auth` attribute) is left alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class _I2:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def audit_hook(services: Any, state: _I2) -> Any:
    """`(action, detail)` → an `audit` row (names only), scheduled on the running loop."""

    def hook(action: str, detail: dict[str, Any]) -> None:
        audit = getattr(services, "audit", None)
        if audit is None:
            return
        try:
            state.spawn(audit.record(session_id=None, actor="gateway", action=action, detail=detail))
        except RuntimeError:  # no running loop (sync context): drop rather than fail the call
            log.debug("gateway audit %s dropped (no loop)", action)

    return hook


async def start(services: Any) -> _I2 | None:
    gw = getattr(services, "gateway", None)
    auth = getattr(gw, "auth", None)
    if gw is None or auth is None:
        return None
    state = _I2()
    auth.attach(services.secrets, audit_hook(services, state))
    budgets = getattr(gw, "budgets", None)
    if budgets is not None and budgets.active:
        from auoreo.gateway.budgets import seed_from_ledger

        try:
            await seed_from_ledger(budgets, services.db)
        except Exception:  # a ledger hiccup must not stop the server; ceilings start at 0
            log.exception("could not seed budget spend from the usage ledger")
    ort = services.settings.i2.openrouter
    uses_or = any(m.startswith("openrouter/") for g in services.settings.models.values() for m in g.models)
    if ort.live_catalog and uses_or:
        from auoreo.gateway.openrouter import fetch_catalog

        state.spawn(fetch_catalog(ort.catalog_ttl_s))
    return state


__all__ = ["audit_hook", "start"]
