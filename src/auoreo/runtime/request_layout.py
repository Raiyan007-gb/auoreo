"""Request layout for the agent loop: what one model step sends, and how stable its prefix is.

`build()` freezes the request of a step: the system prompt, the session history with this
turn's `<context>` block on its user message, an optional request-only note, then the
request-time repairs (`runtime/sequence.py`, `runtime/multimodal.py`).

M7 W1b (`docs/designs/m7-token-economy.md`), off by default:

* `m7.token.telemetry`: `PrefixTracker` compares the hash chain of each main-turn request
  (`gateway/caching.py`, carried back on `Usage.cache`) with the previous one of the session and
  reports a broken prefix as a `cache.prefix_changed` warning; the hashes ride the `usage` event
  and `step.started` counts the deferred tools still hidden and those loaded so far.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.gateway.caching import CacheInfo, divergence
from auoreo.gateway.types import LLMRequest, tool_schema
from auoreo.runtime.compaction_plan import turn_index
from auoreo.runtime.multimodal import prepare_messages
from auoreo.runtime.sequence import repair

if TYPE_CHECKING:
    from auoreo.gateway.types import Usage
    from auoreo.harness.loader import ToolDef
    from auoreo.harness.orchestrator import AgentSpec
    from auoreo.runtime.runner import AgentRunner, SessionRuntime
    from auoreo.tools.context import SessionState

TRACKED_SESSIONS = 512


def build(
    runner: AgentRunner,
    spec: AgentSpec,
    rt: SessionRuntime,
    tools: list[ToolDef],
    context: str,
    turn_msg: dict[str, Any],
    last_step: bool,
    run_id: str,
    note: str | None = None,
) -> LLMRequest:
    """The request of one step (re-rendered after a compaction: history indices move)."""
    msgs: list[dict[str, Any]] = [{"role": "system", "content": spec.system_prompt}]
    turn_user_idx = turn_index(rt.history, turn_msg)
    for i, m in enumerate(rt.history):
        if i == turn_user_idx and context:
            m = {**m, "content": f"<context>\n{context}\n</context>\n\n{m['content']}"}
        msgs.append(m)
    if note:
        msgs.append({"role": "user", "content": note})
    msgs = prepare_messages(repair(msgs), runner.s.gateway.supports_vision(spec.model))
    variant = spec.variant
    temperature = spec.agent.manifest.temperature
    if temperature is None and variant is not None:
        temperature = variant.temperature
    return LLMRequest(
        model=spec.model,
        messages=tuple(msgs),
        tools=tuple(tool_schema(t.name, t.manifest.description, t.manifest.parameters) for t in tools),
        tool_choice="none" if last_step else "auto",
        temperature=temperature,
        omit_fields=tuple(variant.omit_fields) if variant else (),
        reasoning_effort=runner.policy.effort(run_id, spec.agent.manifest.effort),
        metadata={
            "session_id": rt.session_id,
            "run_id": run_id,
            **runner.compaction.post.marks(rt.session_id),
        },
        variant=variant,
        turn_state=run_id,
        volatile_tail=bool(note),
    )


# ------------------------------------------------------------------ telemetry
@dataclass(frozen=True)
class _Seen:
    deployment: str
    tools_hash: str
    hashes: tuple[str, ...]


class PrefixTracker:
    """The last main-turn request of each session, as a hash chain (bounded, least recent out).

    `observe` returns the `cache.prefix_changed` payload when the new request does not extend the
    previous one: `index` is the first message that differs and `expected_index` the last message
    of the previous request (`len(previous) - 1`), all of which should have been a prefix. A new
    deployment (model switch, fallback), a changed tool list or a compaction (`reset`) start a new
    baseline instead: those misses are expected and visible through `tools.hash` and the hit ratio.
    """

    def __init__(self, capacity: int = TRACKED_SESSIONS) -> None:
        self.capacity = capacity
        self._seen: OrderedDict[str, _Seen] = OrderedDict()

    def __len__(self) -> int:
        return len(self._seen)

    def forget(self, session_id: str) -> None:
        self._seen.pop(session_id, None)

    def observe(
        self,
        session_id: str,
        deployment: str,
        info: CacheInfo,
        *,
        volatile_tail: bool = False,
        reset: bool = False,
    ) -> dict[str, Any] | None:
        prev = self._seen.pop(session_id, None)
        stable = info.prefix_hashes[:-1] if volatile_tail else info.prefix_hashes
        self._seen[session_id] = _Seen(deployment, info.tools_hash, stable)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        if prev is None or reset or (prev.deployment, prev.tools_hash) != (deployment, info.tools_hash):
            return None
        index = divergence(prev.hashes, info.prefix_hashes)
        if index >= len(prev.hashes):
            return None
        return {
            "index": index,
            "expected_index": len(prev.hashes) - 1,
            "previous_messages": len(prev.hashes),
            "messages": len(info.prefix_hashes),
            "role": info.roles[index] if index < len(info.roles) else None,
        }


async def cache_fields(
    runner: AgentRunner, sid: str, run_id: str, request: LLMRequest, track: bool, usage: Usage
) -> dict[str, Any]:
    """Extra `usage` event fields for one call ({} with telemetry off). A tracked call (the
    step's own request, not a recovery nudge) is also compared with the session's previous one."""
    info = usage.cache
    if info is None or not runner.s.settings.m7.token.telemetry:
        return {}
    if track and "purpose" not in request.metadata:
        change = runner.prefix.observe(
            sid,
            usage.deployment or request.model,
            info,
            volatile_tail=request.volatile_tail,
            reset="post_compaction" in request.metadata,
        )
        if change is not None:
            data = {"run_id": run_id, "model": request.model, **change}
            await runner.s.emit(sid, "cache.prefix_changed", data)
    return info.event_data()


def tool_counts(runner: AgentRunner, spec: AgentSpec, state: SessionState) -> dict[str, int]:
    """`step.started` extras: deferred tools still hidden, and those loaded by `tool_search`."""
    if not runner.s.settings.m7.token.telemetry:
        return {}
    loaded = sum(1 for t in spec.deferred if t.name in state.loaded_tools)
    return {"deferred": len(spec.deferred) - loaded, "loaded": loaded}


__all__ = ["PrefixTracker", "build", "cache_fields", "tool_counts"]
