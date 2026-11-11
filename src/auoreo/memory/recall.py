"""Which memory service a turn uses, and its recall block (M7 W3, per-agent tenants).

The runner resolves the service once per turn from the agent manifest and uses it for the recall
block, both `record_message` calls and `ctx.services["memory"]`, so the `memory`,
`knowledge_search` and `session_search` tools land in the same tenant. Compaction resolves it
from the session's agent name. With `m7.agents.memory_tenants` off, or for an agent without
`memory_tenant`, every function here returns the shared `local` service: today's behaviour.

Design: docs/designs/m7-agents-bots.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)


async def memory_for(services: Services, manifest: Any) -> Any:
    """The memory service for an agent manifest; None when memory is disabled.

    Fails closed: if the agent's tenant cannot be opened the turn runs without memory rather
    than reading and writing the shared `local` tenant.
    """
    mem = services.memory
    wanted = getattr(manifest, "memory_tenant", None) and services.settings.m7.agents.memory_tenants
    if mem is None or not wanted:
        return mem  # flag off or no tenant: the shared service, untouched
    try:
        return await mem.for_agent(manifest)
    except Exception:
        log.exception("memory tenant unavailable; this turn runs without memory")
        return None


TOPICS_REFUSED = (
    "topic files are shared by every agent and this agent has a memory tenant; "
    "store it with target=fact instead (facts stay in the agent's tenant)"
)


def tenant_of(tool_services: Any) -> str | None:
    """The calling agent's `memory_tenant` when tenants apply (`m7.agents.memory_tenants` on),
    from a tool context's `services` map. Topic files have no tenant, so the `memory` and
    `memory_read` tools refuse them to such an agent (even when its tenant failed to open)."""
    svc = tool_services.get("services")
    manifest = getattr(getattr(tool_services.get("spec"), "agent", None), "manifest", None)
    slug = getattr(manifest, "memory_tenant", None)
    if not slug or svc is None or not svc.settings.m7.agents.memory_tenants:
        return None
    return str(slug)


async def memory_for_agent_name(services: Services, agent: str) -> Any:
    """`memory_for` by agent name (compaction indexing); an unknown agent uses `local`."""
    try:
        manifest = services.harness.agent(agent).manifest
    except KeyError:
        return services.memory
    return await memory_for(services, manifest)


async def recall_block(services: Services, memory: Any, rt: Any, manifest: Any, text: str, top_k: int) -> str:
    """The per-turn memory text (files + recalled items); recall never fails the turn."""
    if memory is None or not manifest.memory:
        return ""
    from auoreo.runtime.nudges import recall_query

    try:
        query = await recall_query(services, rt, text)  # --- G2b: opt-in rewrite, fail-open
        return str(await memory.context_block(query, limit=top_k, session_id=rt.session_id))
    except Exception:
        log.exception("memory recall failed")
        return ""
