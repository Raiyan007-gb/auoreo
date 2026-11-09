"""I3 spawn extras (spec §16.1, §16.2, §16.4, §25.11, §25.15).

- `subagent_id` = `sa-{n}-{hex8}` (n counts the parent's spawns, from 1);
- member metadata `agent_type/model/color/cwd` (stored on `team_members`);
- `allowedTools` replaces the child's tool selection (unknown names fail the spawn loud);
- MCP tools reach a child only via `inherit_mcp_toolsets` (`true` = all servers, a list =
  those servers); everything else is hidden and refused by the engine;
- usage attribution (`core.usage_attrib`) so the child's model calls carry
  `{parent_session_id, subagent_id, depth, task_name}`;
- the exact fork (`mode: exact`): copy the parent's history + frozen spec into the child.
"""

from __future__ import annotations

import fnmatch
import hashlib
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from auoreo.core import usage_attrib
from auoreo.gateway.types import tool_schema
from auoreo.teams.fork import FORK_DIRECTIVE, ExactSpec, FrozenTurn, cache_key, exact_history
from auoreo.teams.mailbox import TeamsError

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

COLORS = ("blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red")
MCP_PREFIX = "mcp__"


def next_subagent_id(counters: dict[str, int], parent: str) -> str:
    counters[parent] = n = counters.get(parent, 0) + 1
    return f"sa-{n}-{secrets.token_hex(4)}"


def color_for(name: str) -> str:
    return COLORS[int(hashlib.sha256(name.encode()).hexdigest(), 16) % len(COLORS)]


def check_allowed(tools: Mapping[str, Any], allowed: list[str] | None) -> list[str] | None:
    """`allowedTools` entries are tool names or globs; one that matches nothing fails loud."""
    if allowed is None:
        return None
    unknown = [a for a in allowed if not any(fnmatch.fnmatchcase(n, a) for n in tools)]
    if unknown:
        raise TeamsError(f"allowedTools names unknown tools: {', '.join(unknown)} (spawn refused)")
    return list(allowed)


def mcp_deny(tools: Mapping[str, Any], inherit: bool | list[str]) -> list[str]:
    """Deny globs/names for MCP tools the child must not inherit."""
    if inherit is True:
        return []
    if not inherit:
        return [f"{MCP_PREFIX}*"]
    keep = {f"{MCP_PREFIX}{srv}__" for srv in inherit}
    return sorted(n for n in tools if n.startswith(MCP_PREFIX) and not any(n.startswith(k) for k in keep))


def denied_permissions(
    tools: Mapping[str, Any], allowed: list[str] | None, deny: list[str]
) -> frozenset[str]:
    """Permission names the child's engine refuses: tools outside `allowedTools` and denied MCP
    tools — except a permission name an allowed tool still needs."""

    def hit(name: str, globs: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(name, g) for g in globs)

    blocked: set[str] = set()
    needed: set[str] = set()
    for name, tool in tools.items():
        perm = str(getattr(tool.manifest, "permission_name", name))
        out = (allowed is not None and not hit(name, allowed)) or hit(name, deny)
        (blocked if out else needed).add(perm)
    return frozenset(blocked - needed)


def register_usage(m: Member) -> None:
    usage_attrib.register(
        m.session_id,
        parent_session_id=m.parent_session_id,
        subagent_id=m.subagent_id,
        depth=m.depth,
        task_name=m.name,
    )


def member_extras(t: TeamsService, m: Member, manifest: Any, cwd: Any = None) -> dict[str, Any]:
    """`team_members` metadata columns for a new child (spec §16.4)."""
    return {
        "subagent_id": m.subagent_id,
        "agent_type": m.agent,
        "model": m.model or getattr(manifest, "model", None),
        "color": color_for(m.name),
        "cwd": str(cwd or t.s.cwd),  # audit only; the runtime reads `session.created`
    }


async def seed_exact(t: TeamsService, m: Member, frozen: FrozenTurn | None, prompt: str) -> str | None:
    """Copy the parent's turn into the child (`mode: exact`). Returns the child's directive, or
    None on a cache miss (no frozen turn / switched off) so the caller falls back to `fork`."""
    coord = t.s.coordinator
    if frozen is None or not t.s.settings.i3.delegation.fork_cache_exact:
        await t.s.emit(
            m.parent_session_id, "subagent.fork_cache_miss", {"child": m.session_id, "name": m.name}
        )
        return None
    prt = await coord.runtime(m.parent_session_id)
    tools = tuple(coord.runner._available(frozen.spec, prt.state, frozen.options))
    history = exact_history(prt.history, frozen.turn_msg, frozen.context)
    crt = await coord.runtime(m.session_id)
    for msg in history:
        await coord.runner._persist(crt, msg)
    m.exact = ExactSpec(frozen.spec, tools)
    m.model, m.agent = frozen.spec.model, frozen.spec.name  # model=inherit
    schemas = [tool_schema(x.name, x.manifest.description, x.manifest.parameters) for x in tools]
    key = cache_key(frozen.spec.system_prompt, schemas, history)
    t.forks.add(m.session_id)
    await t.s.emit(
        m.parent_session_id,
        "subagent.fork",
        {"child": m.session_id, "name": m.name, "mode": "exact", "cache_key": key, "messages": len(history)},
    )
    return FORK_DIRECTIVE + prompt
