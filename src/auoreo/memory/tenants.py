"""Tenant provisioning (spec §25.8): `team_create` → a `team:<id>` memory tenant.

A bus tap watches `team.created` (emitted by the teams service) and provisions the tenant in
memory.db, so the teams code stays memory-agnostic. Starter: `auoreo.memory.tenants:start`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.schema.events import ServerEvent

log = logging.getLogger(__name__)


class TenantProvisioner:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._tasks: set[asyncio.Task[Any]] = set()
        services.bus.add_tap(self._tap)

    def _tap(self, event: ServerEvent) -> None:
        if event.type != "team.created" or self.s.memory is None:
            return
        team = (event.data or {}).get("team")
        if not team:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._provision(f"team:{team}"))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _provision(self, tenant: str) -> None:
        try:
            if self.s.memory is not None:
                await self.s.memory.provision_tenant(tenant)
        except Exception:
            log.exception("could not provision memory tenant %s", tenant)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        self.s.bus.remove_tap(self._tap)
        await self.drain()


async def start(services: Services) -> TenantProvisioner | None:
    return TenantProvisioner(services) if services.memory is not None else None
