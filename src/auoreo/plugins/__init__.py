"""Out-of-process plugins (spec §15): install, JSON-RPC bridge, connectors, creator mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.plugins.host import PluginHost


async def start(services: Services) -> PluginHost:
    """Extension starter (`core/extensions.STARTERS`); lands in `services.ext["plugins"]`."""
    from auoreo.plugins.host import start as start_host

    return await start_host(services)
