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
* Empty response (no text, no tool calls, not a length cut): `EMPTY_RESPONSE`, retried
  once with a nudge; a second empty reply is returned with `empty=True`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from auoreo.core import observability as obs
from auoreo.core.redaction import StreamRedactor
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Finish, LLMRequest, ReasoningDelta, TextDelta, ToolCall, Usage
from auoreo.runtime.budget import OUT_OF_BAND

if TYPE_CHECKING:
    from auoreo.runtime.budget import TurnBudget
    from auoreo.runtime.runner import AgentRunner, SessionRuntime

OTK_FLOOR = 8_192
OTK_ESCALATED = 65_536
RESUME_NOTE = (
    f"{OUT_OF_BAND} Output token limit hit. Resume directly — no apology, no recap. Pick up "
    "mid-thought exactly where you stopped, and break any remaining work into smaller pieces."
)
EMPTY_NOTE = f"{OUT_OF_BAND} Your previous reply was empty. Reply now: call a tool or give your answer."


@dataclass
class StepOutput:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    finish: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    truncated: bool = False
    empty: bool = False
    continuations: int = 0
    escalated: bool = False
    model_calls: int = 1


Touch = Callable[..., Any]


async def stream(
    runner: AgentRunner, sid: str, run_id: str, request: LLMRequest, touch: Touch | None = None
) -> StepOutput:
    """Stream one call: redacted deltas out, tool calls + usage + finish reason in."""
    s = runner.s
    out = StepOutput()
    parts: list[str] = []
    scrub = StreamRedactor()  # deltas are redacted as one stream, never chunk by chunk
    think = StreamRedactor()  # thinking is its own stream (`message.reasoning`, spec §17)
    purpose = str(request.metadata.get("purpose", "turn"))
    meter = runner.usage.meter(sid, run_id, request.model)  # latency + outcome per call
    async for item in meter.wrap(
        obs.traced_stream(s.gateway.stream(request), model=request.model, purpose=purpose)
    ):
        if touch is not None:
            touch()
        match item:
            case TextDelta(text=t):
                parts.append(t)
                if safe := scrub.feed(t):
                    await s.emit(sid, "message.delta", {"run_id": run_id, "text": safe})
            case ReasoningDelta(text=t):
                if safe := think.feed(t):
                    await s.emit(sid, "message.reasoning", {"run_id": run_id, "text": safe})
            case ToolCall():
                out.calls.append(item)
            case Usage():
                await meter.record(item)
                out.prompt_tokens += item.prompt_tokens
                out.completion_tokens += item.completion_tokens
                await s.emit(
                    sid,
                    "usage",
                    {"run_id": run_id, "model": request.model, **item.event_data()},
                )
            case Finish(reason=reason):
                out.finish = reason
    if thought := think.flush():
        await s.emit(sid, "message.reasoning", {"run_id": run_id, "text": thought})
    if tail := scrub.flush():
        await s.emit(sid, "message.delta", {"run_id": run_id, "text": tail})
    out.text = "".join(parts)
    return out


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
        out = await stream(runner, sid, run_id, request, touch)
    except GatewayError as exc:
        await runner.compaction.recover_overflow(rt, exc, request.model)  # or re-raises
        request = build()
        out = await stream(runner, sid, run_id, request, touch)
    if not out.text and not out.calls and out.finish != "length":
        await runner.s.emit(sid, "run.empty_response", {"run_id": run_id, "code": "EMPTY_RESPONSE"})
        retry = await stream(runner, sid, run_id, _with(request, {"role": "user", "content": EMPTY_NOTE}))
        out = _merge(out, retry, joined=False)
        out.empty = not out.text and not out.calls
    if out.finish == "length" and not out.calls:
        out = await _recover_length(runner, sid, run_id, request, out, touch)
    budget.record(out.prompt_tokens, out.completion_tokens, out.model_calls)
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
        model_calls=old.model_calls + new.model_calls,
        continuations=old.continuations,
        escalated=old.escalated,
    )
