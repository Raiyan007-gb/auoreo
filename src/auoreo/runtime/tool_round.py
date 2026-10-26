"""One tool round of a step (spec §8.3): batch, run, and commit results in model order."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from auoreo.gateway.types import ToolCall
from auoreo.harness.loader import ToolDef
from auoreo.runtime.multimodal import image_message, tool_messages
from auoreo.tools.activity import activity_fields
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.executor import ToolOutcome

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.harness.orchestrator import AgentSpec
    from auoreo.runtime.runner import AgentRunner, SessionRuntime

ABORTED_BEFORE_DISPATCH = "[aborted before dispatch]"


def batches_of(calls: list[ToolCall], tools: dict[str, ToolDef]) -> list[list[ToolCall]]:
    """Consecutive concurrency-safe calls run together; anything else is a barrier."""
    batches: list[list[ToolCall]] = []
    for call in calls:
        t = tools.get(call.name)
        safe = bool(t and t.manifest.is_concurrency_safe)
        if safe and batches and batches[-1] and _all_safe(batches[-1], tools):
            batches[-1].append(call)
        else:
            batches.append([call])
    return batches


async def tool_round(
    runner: AgentRunner,
    rt: SessionRuntime,
    spec: AgentSpec,
    engine: PermissionEngine,
    ctx: ToolContext,
    run_id: str,
    calls: list[ToolCall],
    tools: dict[str, ToolDef],
) -> list[ToolOutcome]:
    """Run `calls` and persist one tool message per call, strictly in model order."""
    s = runner.s
    sem = asyncio.Semaphore(max(1, s.settings.loop.max_parallel_tool_calls))
    outcomes: list[ToolOutcome] = []

    async def one(call: ToolCall) -> ToolOutcome:
        async with sem:
            await s.emit(
                rt.session_id,
                "tool.started",
                {
                    "run_id": run_id,
                    "call_id": call.id,
                    "tool": call.name,
                    "arguments": call.arguments[:4000],
                    **activity_fields(tools.get(call.name), call.arguments),  # --- I6: activityHint
                },
            )
            return await runner.executor.run(call, tools=tools, ctx=ctx, engine=engine)

    vision = s.gateway.supports_vision(spec.model)
    image_parts: list[dict[str, Any]] = []  # sent after ALL tool messages of the round
    for batch in batches_of(calls, tools):
        results = await asyncio.gather(*(one(c) for c in batch))
        for o in results:
            tool_msg, parts = tool_messages(o.call.id, o.result, vision)
            await runner._persist(rt, tool_msg)
            image_parts.extend(parts)
            await s.emit(
                rt.session_id,
                "tool.completed",
                {
                    "run_id": run_id,
                    "call_id": o.call.id,
                    "tool": o.call.name,
                    "is_error": o.result.is_error,
                    "content": o.result.content[:4000],
                    "meta": o.result.meta,
                },
            )
            outcomes.append(o)
        if any(o.halt for o in results):
            break  # do not start later calls after a rejection
    for call in calls[len(outcomes) :]:  # unstarted calls after a halt
        aborted = {"role": "tool", "tool_call_id": call.id, "content": ABORTED_BEFORE_DISPATCH}
        await runner._persist(rt, aborted)
        outcomes.append(ToolOutcome(call, ToolResult(ABORTED_BEFORE_DISPATCH, is_error=True)))
    if image_parts:
        await runner._persist(rt, image_message(image_parts))
    return outcomes


def _all_safe(batch: list[ToolCall], tools: dict[str, ToolDef]) -> bool:
    return all((t := tools.get(c.name)) is not None and t.manifest.is_concurrency_safe for c in batch)
