"""Lifespan teardown in the spec §24.7 order: stop the cron tick → drain the delivery queue
(≤ 5 s) → close WebSockets (1001) → end SSE streams → `svc.close()` (extensions, audit/event
flush, DB). Each step is best effort: one failure never skips the rest."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)
DRAIN_S = 5.0


async def stop_cron(svc: Any) -> None:
    sched = svc.ext.get("scheduler")
    if sched is not None and hasattr(sched, "close"):
        await sched.close()  # idempotent; extensions.close_all calls it again later


async def drain_deliveries(svc: Any, timeout_s: float = DRAIN_S, poll_s: float = 0.1) -> bool:
    """Wait until no outbox row is `delivering` (True) or the timeout passes (False)."""
    worker = svc.ext.get("deliveries")
    outbox = getattr(worker, "outbox", None)
    if outbox is None or not getattr(worker, "active", False):
        return True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        if not await outbox.list(status="delivering", limit=1):
            return True
        if loop.time() >= deadline:
            log.warning("shutdown: deliveries still in flight after %.0fs", timeout_s)
            return False
        await asyncio.sleep(poll_s)


async def teardown(app: Any, svc: Any, *, owned: bool, drain_s: float = DRAIN_S) -> None:
    steps: list[tuple[str, Any]] = []
    if owned:
        steps += [
            ("stop cron", lambda: stop_cron(svc)),
            ("drain deliveries", lambda: drain_deliveries(svc, drain_s)),
        ]
    steps.append(("close websockets", lambda: svc.connections.close_all(1001)))
    for name, step in steps:
        try:
            await step()
        except Exception:
            log.exception("shutdown step '%s' failed", name)
    app.state.shutting_down = True  # SSE loops see it within 1 s and end their stream
    if owned:
        await svc.close()
