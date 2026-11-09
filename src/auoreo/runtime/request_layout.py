"""Request layout for the agent loop: what one model step sends.

`build()` freezes the request of a step: the system prompt, the session history with this
turn's `<context>` block on its user message, an optional request-only note, then the
request-time repairs (`runtime/sequence.py`, `runtime/multimodal.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from auoreo.gateway.types import LLMRequest, tool_schema
from auoreo.runtime.compaction_plan import turn_index
from auoreo.runtime.multimodal import prepare_messages
from auoreo.runtime.sequence import repair

if TYPE_CHECKING:
    from auoreo.harness.loader import ToolDef
    from auoreo.harness.orchestrator import AgentSpec
    from auoreo.runtime.runner import AgentRunner, SessionRuntime


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
    )


__all__ = ["build"]
