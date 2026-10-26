"""Per-session state kept in the append-only `events` table (no extra columns needed).

* Source overrides (spec §17 "per-session enable"): `session.sources_changed{disabled[]}`
  lists `mcp:<server>` / `plugin:<name>` sources switched off for one session. The runner
  drops their tools and skills from the turn's `AgentSpec` (tool selection and the `skill`
  tool both honour it); the newest event wins.
* The current plan (spec §2.3): the newest `plan.updated{steps, explanation}` event.
* `@skill` mentions (spec §15): a user message naming `@skill-name` loads that skill for the
  turn, injected as a user message right after it (same shape as the `skill` tool).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import EventRow
from auoreo.harness.skill_sources import mention_messages

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.harness.orchestrator import AgentSpec

SOURCES_EVENT = "session.sources_changed"
PLAN_EVENT = "plan.updated"
SOURCE_KINDS = ("mcp", "plugin")


async def latest_event(services: Services, session_id: str, type_: str) -> dict[str, Any] | None:
    q = (
        select(EventRow)
        .where(EventRow.aggregate_id == session_id, EventRow.type == type_)
        .order_by(EventRow.seq.desc())
        .limit(1)
    )
    async with services.db.session() as s:
        row = (await s.execute(q)).scalar_one_or_none()
    return None if row is None else services.events.plain(row.data)


async def disabled_sources(services: Services, session_id: str) -> list[str]:
    data = await latest_event(services, session_id, SOURCES_EVENT)
    return sorted(str(x) for x in (data or {}).get("disabled", []))


def valid_source(source: str) -> bool:
    kind, _, name = source.partition(":")
    return kind in SOURCE_KINDS and bool(name)


async def set_disabled(services: Services, session_id: str, disabled: list[str]) -> list[str]:
    bad = [s for s in disabled if not valid_source(s)]
    if bad:
        raise ValueError(f"sources must look like 'mcp:<server>' or 'plugin:<name>': {bad}")
    clean = sorted(set(disabled))
    await services.emit(session_id, SOURCES_EVENT, {"disabled": clean})
    return clean


def tool_source(tool: Any) -> str:
    """`mcp:<server>` / `plugin:<name>` for dynamic tools (their toolset), else the root."""
    toolset = str(getattr(tool.manifest, "toolset", "") or "")
    return toolset if valid_source(toolset) else str(tool.source)


def without_sources(spec: AgentSpec, disabled: list[str]) -> AgentSpec:
    if not disabled:
        return spec
    off = set(disabled)
    return dataclasses.replace(
        spec,
        tools=tuple(t for t in spec.tools if tool_source(t) not in off),
        deferred=tuple(t for t in spec.deferred if tool_source(t) not in off),
        skills=tuple(s for s in spec.skills if s.source not in off),
        extras={**spec.extras, "disabled_sources": tuple(sorted(off))},
    )


async def apply_session_sources(services: Services, session_id: str, spec: AgentSpec) -> AgentSpec:
    return without_sources(spec, await disabled_sources(services, session_id))


async def current_plan(services: Services, session_id: str) -> dict[str, Any]:
    rt = getattr(services.coordinator, "_runtimes", {}).get(session_id)
    data = await latest_event(services, session_id, PLAN_EVENT) or {}
    steps = list(rt.state.plan) if rt is not None and rt.state.plan else list(data.get("steps", []))
    return {
        "steps": steps,
        "explanation": str(data.get("explanation", "")),
        "plan_mode": bool(rt.state.plan_mode) if rt is not None else False,
    }


async def inject_mentions(runner: Any, rt: Any, spec: AgentSpec, text: str, run_id: str) -> list[str]:
    """Persist one `<skill>` user message per `@skill` mention; returns the loaded names."""
    loaded: list[str] = []
    for name, message in await mention_messages(spec, runner.s.harness, text):
        await runner._persist(rt, {"role": "user", "content": message})
        await runner.s.emit(rt.session_id, "skill.loaded", {"run_id": run_id, "name": name, "via": "mention"})
        loaded.append(name)
    return loaded
