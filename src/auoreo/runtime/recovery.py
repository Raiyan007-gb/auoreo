"""One model call of a step with its recoveries (spec §8.6).

* Context overflow (`CONTEXT` gateway error): compact (retain=0) and retry once — the
  existing compaction path.
* Output-token limit (`Finish("length")` with no tool calls): first re-issue the same
  request once with `max_tokens` 8k → 64k (`otk_escalate`, no meta message; the partial is
  discarded and a `message.reset` event tells clients to drop the draft); then up to
  `mot_recovery_limit` resume nudges (meta: never persisted) whose outputs are joined into
  the same assistant message, each with a 2/4/8/16× max_tokens boost capped at
  `length_boost_cap` and never below the original floor. Success emits
  `run.length_recovered`; giving up returns the joined text with `truncated=True`.
* Empty response (no usable text, no valid tool calls, not a length cut): classified by
  `runtime.empty` (truly-empty / whitespace / thinking-only / invalid-tool-call / refusal)
  and recovered per class (thinking-only gets ×2 answer-prefill retries, refusals none);
  still empty → returned with `empty=True` and persisted as the `(empty)` sentinel.
* Streamed tool-call fragments become `tool.partial{call_id, arg_delta, seq}` events
  (render-only; never executed) and abandoned ones `tool.partial_drop{call_ids}` (spec §25.6).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from auoreo.core import observability as obs
from auoreo.core.redaction import StreamRedactor
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import (
    Finish,
    LLMRequest,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallDrop,
    Usage,
)
from auoreo.memory.scrubber import StreamingContextScrubber
from auoreo.runtime import request_layout as layout
from auoreo.runtime.budget import OUT_OF_BAND
from auoreo.runtime.empty import NOTES, attempts, classify, valid_call

if TYPE_CHECKING:
    from auoreo.runtime.budget import TurnBudget
    from auoreo.runtime.runner import AgentRunner, SessionRuntime

OTK_FLOOR = 8_192
OTK_ESCALATED = 65_536
RESUME_NOTE = (
    f"{OUT_OF_BAND} Output token limit hit. Resume directly — no apology, no recap. Pick up "
    "mid-thought exactly where you stopped, and break any remaining work into smaller pieces."
)
EMPTY_NOTE = NOTES["truly_empty"]


@dataclass
class StepOutput:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    finish: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    truncated: bool = False
    empty: bool = False
    continuations: int = 0
    escalated: bool = False
    model_calls: int = 1
    reasoning: str = ""
    empty_class: str | None = None


Touch = Callable[..., Any]
CacheFields = Callable[[Usage], Awaitable[dict[str, Any]]]


class _Collect:
    """Per-call stream state: redacted deltas out, tool calls + usage + finish reason in."""

    def __init__(
        self, s: Any, sid: str, run_id: str, request: LLMRequest, meter: Any, cache: CacheFields
    ) -> None:
        self.s, self.sid, self.run_id, self.request, self.meter = s, sid, run_id, request, meter
        self.cache = cache  # --- M7: prefix/tool hashes for the `usage` event ({} when off)
        self.out = StepOutput()
        self.parts: list[str] = []
        self.thoughts: list[str] = []
        self.partial: dict[str, None] = {}  # call ids with streamed fragments
        self.scrub = StreamRedactor()  # deltas are redacted as one stream, never chunk by chunk
        self.recall = StreamingContextScrubber()  # §24.3: a leaked <memory-context> never streams
        self.think = StreamRedactor()  # thinking is its own stream (`message.reasoning`, spec §17)

    async def emit(self, type_: str, data: dict[str, Any]) -> None:
        await self.s.emit(self.sid, type_, {"run_id": self.run_id, **data})

    async def feed(self, item: Any) -> None:
        out = self.out
        match item:
            case TextDelta(text=t):
                t = self.recall.feed(t)
                self.parts.append(t)
                if safe := self.scrub.feed(t):
                    await self.emit("message.delta", {"text": safe})
            case ReasoningDelta(text=t):
                self.thoughts.append(t)
                if safe := self.think.feed(t):
                    await self.emit("message.reasoning", {"text": safe})
            case ToolCallDelta(call_id=cid, name=name, arg_delta=delta, seq=seq):
                self.partial[cid] = None  # render-only; partial args are never logged
                await self.emit(
                    "tool.partial", {"call_id": cid, "name": name, "arg_delta": delta, "seq": seq}
                )
            case ToolCallDrop(call_ids=ids):
                await self.drop([c for c in ids if c in self.partial] or list(ids))
            case ToolCall():
                out.calls.append(item)
            case Usage():
                await self.meter.record(item)
                out.prompt_tokens += item.prompt_tokens
                out.completion_tokens += item.completion_tokens
                out.cached_tokens += item.cached_tokens
                out.cache_write_tokens += item.cache_write_tokens
                data = {"model": self.request.model, **item.event_data(), **await self.cache(item)}
                await self.emit("usage", data)
            case Finish(reason=reason):
                out.finish = reason

    async def drop(self, ids: list[str]) -> None:
        for cid in ids:
            self.partial.pop(cid, None)
        if ids:
            await self.emit("tool.partial_drop", {"call_ids": ids})

    async def close(self) -> StepOutput:
        committed = {c.id for c in self.out.calls if valid_call(c)}
        await self.drop([c for c in self.partial if c not in committed])
        if thought := self.think.flush():
            await self.emit("message.reasoning", {"text": thought})
        if rest := self.recall.flush():  # a held partial tag that never completed
            self.parts.append(rest)
            if safe := self.scrub.feed(rest):
                await self.emit("message.delta", {"text": safe})
        if tail := self.scrub.flush():
            await self.emit("message.delta", {"text": tail})
        self.out.text = "".join(self.parts)
        self.out.reasoning = "".join(self.thoughts)
        return self.out


async def stream(
    runner: AgentRunner,
    sid: str,
    run_id: str,
    request: LLMRequest,
    touch: Touch | None = None,
    *,
    track: bool = False,
) -> StepOutput:
    """Stream one call: redacted deltas out, tool calls + usage + finish reason in. `track`
    marks the step's own request (not a recovery nudge) for the M7 prefix tracker."""
    s = runner.s
    purpose = str(request.metadata.get("purpose", "turn"))
    meter = runner.usage.meter(sid, run_id, request.model)  # latency + outcome per call
    cache = partial(layout.cache_fields, runner, sid, run_id, request, track)
    col = _Collect(s, sid, run_id, request, meter, cache)
    try:
        async for item in meter.wrap(
            obs.traced_stream(s.gateway.stream(request), model=request.model, purpose=purpose)
        ):
            if touch is not None:
                touch()
            await col.feed(item)
    except GatewayError:
        await col.drop(list(col.partial))  # the gateway failed without its own drop
        raise
    return await col.close()


async def model_step(
    runner: AgentRunner,
    rt: SessionRuntime,
    run_id: str,
    build: Callable[[], LLMRequest],
    budget: TurnBudget,
    touch: Touch | None = None,
) -> StepOutput:
    sid = rt.session_id
    request = build()
    try:
        out = await stream(runner, sid, run_id, request, touch, track=True)
    except GatewayError as exc:
        await runner.compaction.recover_overflow(rt, exc, request.model)  # or re-raises
        request = build()
        out = await stream(runner, sid, run_id, request, touch, track=True)
    if (kind := classify(out.text, out.calls, out.reasoning, out.finish)) is not None:
        out = await _recover_empty(runner, sid, run_id, request, out, kind)
    if out.finish == "length" and not out.calls:
        out = await _recover_length(runner, sid, run_id, request, out, touch)
    budget.record(
        out.prompt_tokens,
        out.completion_tokens,
        out.model_calls,
        cached=out.cached_tokens,
        cache_write=out.cache_write_tokens,
    )
    return out


async def _recover_empty(
    runner: AgentRunner, sid: str, run_id: str, request: LLMRequest, out: StepOutput, kind: str
) -> StepOutput:
    """Per-class recovery (spec §25.6 L969); still empty → `empty=True` + `empty_class`."""
    first = kind
    tries = attempts(kind, runner.s.settings.i2.thinking_prefill_retries)  # type: ignore[arg-type]
    for i in range(tries):
        await runner.s.emit(
            sid,
            "run.empty_response",
            {"run_id": run_id, "code": "EMPTY_RESPONSE", "class": kind, "attempt": i + 1},
        )
        retry = await stream(runner, sid, run_id, _with(request, {"role": "user", "content": NOTES[kind]}))
        out = _merge(out, retry, joined=False)
        if (nxt := classify(out.text, out.calls, out.reasoning, out.finish)) is None:
            return out
        kind = nxt
    if not tries:
        await runner.s.emit(
            sid, "run.empty_response", {"run_id": run_id, "code": "EMPTY_RESPONSE", "class": kind}
        )
    out.calls = [c for c in out.calls if valid_call(c)]
    out.empty, out.empty_class = True, kind or first
    return out


async def _recover_length(
    runner: AgentRunner, sid: str, run_id: str, request: LLMRequest, out: StepOutput, touch: Touch | None
) -> StepOutput:
    cfg = runner.s.settings.loop
    floor = request.max_tokens or OTK_FLOOR
    if cfg.otk_escalate and floor < OTK_ESCALATED:
        await runner.s.emit(sid, "message.reset", {"run_id": run_id, "reason": "otk_escalate"})
        again = await stream(runner, sid, run_id, replace(request, max_tokens=OTK_ESCALATED), touch)
        out = _merge(out, again, joined=False)
        out.escalated = True
    k = 0
    while out.finish == "length" and not out.calls and k < cfg.mot_recovery_limit:
        k += 1
        boost = max(floor, min(cfg.length_boost_cap, floor * 2**k))
        nudge = _with(
            request, {"role": "assistant", "content": out.text}, {"role": "user", "content": RESUME_NOTE}
        )
        more = await stream(runner, sid, run_id, replace(nudge, max_tokens=boost), touch)
        out = _merge(out, more, joined=True)
        out.continuations = k
    if out.finish == "length" and not out.calls:
        out.truncated = True
        await runner.s.emit(
            sid,
            "run.length_truncated",
            {"run_id": run_id, "continuations": out.continuations, "escalated": out.escalated},
        )
    elif out.escalated or out.continuations:
        await runner.s.emit(
            sid,
            "run.length_recovered",
            {"run_id": run_id, "continuations": out.continuations, "escalated": out.escalated},
        )
    return out


def _with(request: LLMRequest, *extra: dict[str, Any]) -> LLMRequest:
    return replace(request, messages=(*request.messages, *extra))


def _merge(old: StepOutput, new: StepOutput, *, joined: bool) -> StepOutput:
    """`new` continues (`joined`) or replaces `old`; tokens and calls always add up."""
    return replace(
        new,
        text=old.text + new.text if joined else new.text,
        prompt_tokens=old.prompt_tokens + new.prompt_tokens,
        completion_tokens=old.completion_tokens + new.completion_tokens,
        cached_tokens=old.cached_tokens + new.cached_tokens,
        cache_write_tokens=old.cache_write_tokens + new.cache_write_tokens,
        model_calls=old.model_calls + new.model_calls,
        continuations=old.continuations,
        escalated=old.escalated,
    )
