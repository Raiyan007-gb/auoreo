"""Provider stream → `StreamItem`s (spec §10.8, §25.6). No `litellm` here (duck-typed chunks).

`assemble()` turns OpenAI-shaped stream chunks into text / reasoning deltas, streamed
tool-call fragments (`ToolCallDelta`, `tool.partial`), assembled `ToolCall`s, one `Usage`
(estimated when the provider sent none) and the `Finish`. A failure after partial tool-call
fragments were streamed yields `ToolCallDrop` before the typed error propagates, so clients
can tear the drafts down. Thinking-only replies are NOT an error here (the loop classifies
empty replies, spec §25.6 L969); a reply with nothing at all raises `EMPTY_RESPONSE`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

from auoreo.gateway.errors import GatewayError
from auoreo.gateway.pipeline import idle_chunks
from auoreo.gateway.types import (
    Finish,
    LLMRequest,
    ReasoningDelta,
    StreamItem,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallDrop,
    Usage,
    prompt_chars,
)
from auoreo.gateway.usage_parse import parse_usage, reasoning_of


def served_model(resp: Any, chunk: Any, deployments: list[str]) -> str | None:
    """Which deployment answered: LiteLLM hidden params, else the chunk's `model`, matched
    against the candidate deployments (suffix match: providers echo bare ids)."""
    hidden = getattr(resp, "_hidden_params", None) or getattr(chunk, "_hidden_params", None) or {}
    names = [hidden.get("litellm_model_name"), hidden.get("model"), getattr(chunk, "model", None)]
    for name in (str(n) for n in names if n):
        for dep in deployments:
            if dep == name or dep.endswith("/" + name) or name.endswith("/" + dep.split("/", 1)[-1]):
                return dep
    return deployments[0] if len(deployments) == 1 else None


async def assemble(
    resp: Any,
    request: LLMRequest,
    *,
    idle_s: float,
    deployments: list[str],
    map_error: Callable[[BaseException], GatewayError],
) -> AsyncIterator[StreamItem]:
    calls: dict[int, dict[str, str]] = {}
    seen_usage: Usage | None = None
    text_len, seq = 0, 0
    finish = "stop"
    got_any = thought_any = False
    generation: str | None = None
    served: str | None = None
    try:
        async for chunk in idle_chunks(resp, idle_s):
            cid = getattr(chunk, "id", None)
            if isinstance(cid, str) and cid.startswith("gen-"):
                generation = cid
            if served is None:
                served = served_model(resp, chunk, deployments)
            if parsed := parse_usage(getattr(chunk, "usage", None)):
                seen_usage = parsed
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            for thought in reasoning_of(delta):
                thought_any = True
                yield ReasoningDelta(thought)
            if delta is not None and delta.content:
                got_any = True
                text_len += len(delta.content)
                yield TextDelta(delta.content)
            for tc in getattr(delta, "tool_calls", None) or []:
                got_any = True
                idx = tc.index or 0
                slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                slot["id"] = tc.id or slot["id"]
                frag = ""
                if tc.function is not None:
                    slot["name"] += tc.function.name or ""
                    frag = tc.function.arguments or ""
                    slot["args"] += frag
                seq += 1
                yield ToolCallDelta(slot["id"] or f"call_{idx}", slot["name"], frag, seq)
            if choice.finish_reason:
                finish = choice.finish_reason
    except Exception as exc:  # mid-stream failures surface as typed errors
        if calls:
            yield ToolCallDrop(tuple(c["id"] or f"call_{i}" for i, c in sorted(calls.items())))
        raise map_error(exc) from exc
    if not got_any and not thought_any:
        raise GatewayError("EMPTY_RESPONSE", "model returned no content")
    for idx in sorted(calls):
        c = calls[idx]
        yield ToolCall(id=c["id"] or f"call_{idx}", name=c["name"], arguments=c["args"] or "{}")
    if seen_usage is None:  # stream ended without usage: estimate, and say so (§10.8)
        prompt = prompt_chars(request.messages)
        seen_usage = Usage(prompt // 4, max(1, text_len // 4), estimated=True)
    tags = request.metadata.get("tags") or ()
    yield replace(
        seen_usage,
        generation_id=generation,
        deployment=served,
        tags=tuple(str(t) for t in tags) if isinstance(tags, list | tuple) else (),
    )
    yield Finish(finish)


__all__ = ["assemble", "served_model"]
