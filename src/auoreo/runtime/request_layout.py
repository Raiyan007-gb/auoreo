"""Request layout for the agent loop: what one model step sends, and how stable its prefix is.

`build()` freezes the request of a step: the system prompt, the session history with each
turn's `<context>` block on its user message, an optional request-only note, then the
request-time repairs (`runtime/sequence.py`, `runtime/multimodal.py`).

M7 W1b (`docs/designs/m7-token-economy.md`), off by default:

* `m7.token.context_carrier`: where the per-turn context block (date, memory recall, permission
  reminder, locale, tone, exposed variables) lives. `splice` adds it to the current turn's user
  message at request time only; the next turn sends that message without it, so the cached
  prefix breaks there. `persisted` stores the rendered block WITH the turn's user message, under
  the message-level key `CONTEXT_KEY`, and `wire()` renders it the same way in every later
  request, so the prefix only grows. The key is structural: `content` stays exactly what the user
  sent, so everything that reads `content` (transcripts, exports, fork seeds, the messages API,
  memory) is unaffected, and the key never reaches a provider (`wire` drops it). Trade-offs: each
  turn keeps its block until compaction (more prompt tokens, read from the cache), old blocks go
  stale (the date or reminder of that turn), and recalled memories and exposed variable values
  are copied into `messages` rows and JSON exports, outside memory deletion and variable
  rotation. Rollback: a build without this module ignores the key when rendering but sends it to
  the provider as an unknown message field; switch back to `splice` and compact first.

* `m7.token.telemetry`: `PrefixTracker` compares the hash chain of each main-turn request
  (`gateway/caching.py`, carried back on `Usage.cache`) with the previous one of the session and
  reports a broken prefix as a `cache.prefix_changed` warning; the hashes ride the `usage` event
  and `step.started` counts the deferred tools still hidden and those loaded so far.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import MessageRow
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

log = logging.getLogger(__name__)

TRACKED_SESSIONS = 512
CONTEXT_KEY = "auoreo_context"
"""Message-level key holding a turn's rendered context block (`context_carrier = persisted`)."""
CARRY_SCAN = 64  # rows searched (newest first) for the turn's message when attaching its block


# ------------------------------------------------------------------ context carrier
def has_context(message: dict[str, Any]) -> bool:
    """Whether `message` carries a persisted context block."""
    return CONTEXT_KEY in message


def strip_context(message: dict[str, Any]) -> dict[str, Any]:
    """`message` without its context block (the same object when it has none)."""
    if CONTEXT_KEY not in message:
        return message
    return {k: v for k, v in message.items() if k != CONTEXT_KEY}


def plain(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages for a reader outside the model loop (share link, export, events): no blocks."""
    return [strip_context(m) for m in messages]


def _block(context: str) -> str:
    return f"<context>\n{context}\n</context>"


def wire(message: dict[str, Any]) -> dict[str, Any]:
    """`message` as it is sent: the persisted block rendered in front of the content and the
    key removed (the same object when it has none). Deterministic, so prompt-cache safe."""
    if CONTEXT_KEY not in message:
        return message
    out = strip_context(message)
    context, content = message[CONTEXT_KEY], out.get("content")
    if not context:
        return out
    if isinstance(content, list):
        out["content"] = [{"type": "text", "text": _block(context)}, *content]
    else:
        out["content"] = f"{_block(context)}\n\n{content or ''}"
    return out


async def carry(runner: AgentRunner, rt: SessionRuntime, turn_msg: dict[str, Any], context: str) -> None:
    """`persisted` carrier: attach the turn's rendered block to its user message, in the live
    history (in place: the turn locates its message by identity) and in its `messages` row.
    Runs after memory recall and context rendering, which read the history without the block."""
    if not context or runner.s.settings.m7.token.context_carrier != "persisted":
        return
    before = dict(turn_msg)
    turn_msg[CONTEXT_KEY] = context
    async with runner.s.db.session() as s:
        q = (
            select(MessageRow)
            .where(MessageRow.session_id == rt.session_id)
            .order_by(MessageRow.idx.desc())
            .limit(CARRY_SCAN)
        )
        for row in (await s.execute(q)).scalars():
            if row.message == before:
                row.message = dict(turn_msg)
                await s.commit()
                return
    log.warning("context block of session %s not persisted: its message row was not found", rt.session_id)


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
        if CONTEXT_KEY in m:  # a persisted block (this turn's or an earlier one)
            m = wire(m)
        elif i == turn_user_idx and context:
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


__all__ = [
    "CONTEXT_KEY",
    "PrefixTracker",
    "build",
    "cache_fields",
    "carry",
    "has_context",
    "plain",
    "strip_context",
    "tool_counts",
    "wire",
]
