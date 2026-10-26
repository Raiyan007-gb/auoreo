"""Per-turn memory helpers for the runner (spec §11.6, §11.8).

- `MemoryNudge`: every `compaction.memory_nudge_interval` user turns (default 10, 0 = off)
  a short reminder to save durable facts rides the tier-2 context block (user role, so the
  system prompt stays byte-stable). Skipped for trivial prompts, subagents, unattended
  (cron/webhook: `skip_memory`) turns and agents without the `memory` tool.
- `recall_query`: the query used for memory recall, optionally rewritten by one aux call
  (`compaction.rewrite_memory_query`, see `auoreo.memory.rewrite`); fail-open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from auoreo.memory.service import is_trivial

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.harness.orchestrator import AgentSpec
    from auoreo.runtime.inbox import RunOptions
    from auoreo.runtime.runner import SessionRuntime

MEMORY_TOOL = "memory"
NUDGE = (
    "<memory-nudge>Before you finish this turn, consider whether the conversation so far "
    "revealed durable facts worth keeping (user preferences, project conventions, decisions, "
    "recurring fixes). If so, save them with the `memory` tool; skip anything transient or "
    "already saved. Do not mention this reminder.</memory-nudge>"
)


class MemoryNudge:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.turns: dict[str, int] = {}

    def due(self, rt: SessionRuntime, spec: AgentSpec, text: str, options: RunOptions) -> bool:
        every = self.s.settings.compaction.memory_nudge_interval
        if every <= 0 or rt.subagent or options.skip_memory or is_trivial(text):
            return False
        if MEMORY_TOOL not in {t.name for t in (*spec.tools, *spec.deferred)}:
            return False
        n = self.turns[rt.session_id] = self.turns.get(rt.session_id, 0) + 1
        return n % every == 0

    def apply(self, rt: SessionRuntime, spec: AgentSpec, text: str, options: RunOptions, context: str) -> str:
        if not self.due(rt, spec, text, options):
            return context
        return f"{context}\n\n{NUDGE}" if context else NUDGE


async def recall_query(services: Services, rt: SessionRuntime, text: str) -> str:
    """The memory-recall query for this turn: `text`, or its standalone rewrite."""
    if not services.settings.compaction.rewrite_memory_query or rt.subagent:
        return text
    from auoreo.memory.rewrite import rewrite_query

    recent: list[dict[str, Any]] = rt.history[-7:-1]  # the turn's own message is the query
    return await rewrite_query(services, text, recent)


__all__ = ["NUDGE", "MemoryNudge", "recall_query"]
