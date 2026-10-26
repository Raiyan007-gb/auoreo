"""Sampling with tools + model preferences (spec §25.15 L1074, §26.14).

- `to_messages(params)`: MCP sampling messages → gateway (OpenAI-shape) messages, including
  `tool_use` blocks (assistant `tool_calls`) and `tool_result` blocks (`role: tool`).
- `to_tools(params)`: `params.tools` (MCP `Tool`) → function tool schemas.
- `to_result(...)`: the aux call's text/tool calls → `CreateMessageResult` (no tools) or
  `CreateMessageResultWithTools` with `ToolUseContent` blocks and `stop_reason: toolUse`.
- `select_model(settings, gateway, prefs)`: `modelPreferences` → aux model. Hints are matched
  in order as case-insensitive substrings of the configured routing groups/deployments and the
  aux chain (the session's main model is never picked by a hint); otherwise the dominant
  priority picks a routing group (`intelligencePriority` → smart|large|reasoning,
  `costPriority` → cheap|small, `speedPriority` → fast|small|cheap) when one is configured;
  else plain aux routing (`auxiliary.tasks.sampling` → aux chain → `small`).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    TextContent,
    ToolUseContent,
)

from auoreo.gateway.auxiliary import resolve_aux_model
from auoreo.gateway.resolve import expand_alias, resolve_model
from auoreo.gateway.types import ToolCall, tool_schema

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

PRIORITY_GROUPS: dict[str, tuple[str, ...]] = {
    "intelligence": ("smart", "large", "reasoning"),
    "cost": ("cheap", "small"),
    "speed": ("fast", "small", "cheap"),
}


def _block_text(block: Any) -> str:
    text = getattr(block, "text", None)
    return text if isinstance(text, str) else f"[{getattr(block, 'type', 'content')} omitted]"


def _result_text(block: Any) -> str:
    parts = [_block_text(b) for b in getattr(block, "content", []) or []]
    if getattr(block, "structured_content", None) is not None:
        parts.append(json.dumps(block.structured_content, default=str)[:4000])
    return "\n".join(parts) or "(empty)"


def _convert(role: str, blocks: list[Any]) -> list[dict[str, Any]]:
    uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
    results = [b for b in blocks if getattr(b, "type", None) == "tool_result"]
    rest = [b for b in blocks if b not in uses and b not in results]
    out: list[dict[str, Any]] = [
        {"role": "tool", "tool_call_id": r.tool_use_id, "content": _result_text(r)} for r in results
    ]
    text = "\n".join(_block_text(b) for b in rest)
    if role == "assistant" and uses:
        calls = [
            {"id": u.id, "type": "function", "function": {"name": u.name, "arguments": json.dumps(u.input)}}
            for u in uses
        ]
        out.append({"role": "assistant", "content": text or None, "tool_calls": calls})
    elif rest:
        out.append({"role": "assistant" if role == "assistant" else "user", "content": text})
    return out


def to_messages(params: Any) -> tuple[dict[str, Any], ...]:
    msgs: list[dict[str, Any]] = []
    if params.system_prompt:
        msgs.append({"role": "system", "content": params.system_prompt})
    for m in params.messages:
        blocks = m.content if isinstance(m.content, list) else [m.content]
        msgs.extend(_convert(m.role, blocks))
    return tuple(msgs)


def to_tools(params: Any) -> tuple[dict[str, Any], ...]:
    tools = getattr(params, "tools", None) or []
    return tuple(
        tool_schema(t.name, (t.description or t.name)[:1024], dict(t.input_schema or {"type": "object"}))
        for t in tools
    )


def choice_of(params: Any) -> str:
    """MCP `toolChoice.mode` → gateway `tool_choice` (`required` degrades to `auto`)."""
    mode = getattr(getattr(params, "tool_choice", None), "mode", None)
    return "none" if mode == "none" else "auto"


def to_result(
    text: str, calls: list[ToolCall], model: str, with_tools: bool, finish: str | None
) -> CreateMessageResult | CreateMessageResultWithTools:
    if not with_tools:
        return CreateMessageResult(
            role="assistant", content=TextContent(type="text", text=text), model=model, stop_reason="endTurn"
        )
    content: list[Any] = [TextContent(type="text", text=text)] if text else []
    for c in calls:
        try:
            args = json.loads(c.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        content.append(
            ToolUseContent(
                type="tool_use", id=c.id, name=c.name, input=args if isinstance(args, dict) else {}
            )
        )
    stop = "toolUse" if calls else ("maxTokens" if finish == "length" else "endTurn")
    return CreateMessageResultWithTools(
        role="assistant",
        content=content or [TextContent(type="text", text="")],
        model=model,
        stop_reason=stop,
    )


def _candidates(st: Settings) -> list[str]:
    seen: list[str] = []
    for name, group in st.models.items():
        for c in (name, *group.models):
            if c not in seen:
                seen.append(c)
    for raw in st.auxiliary.text_chain:
        if raw != "main" and (c := expand_alias(st, raw)) not in seen:
            seen.append(c)
    return seen


def select_model(st: Settings, gateway: Any, prefs: Any) -> str:
    fallback = resolve_aux_model(st, "sampling", gateway=gateway)
    if prefs is None:
        return fallback
    main = resolve_model(st)
    has = getattr(gateway, "has_credentials", None)
    for hint in getattr(prefs, "hints", None) or []:
        needle = (getattr(hint, "name", None) or "").lower().strip()
        if not needle:
            continue
        for c in _candidates(st):
            if needle in c.lower() and c != main and (has is None or c in st.models or has(c)):
                return c
    scores = {
        "intelligence": getattr(prefs, "intelligence_priority", None) or 0.0,
        "cost": getattr(prefs, "cost_priority", None) or 0.0,
        "speed": getattr(prefs, "speed_priority", None) or 0.0,
    }
    top = max(scores, key=lambda k: scores[k])
    if scores[top] >= 0.5:
        for group in PRIORITY_GROUPS[top]:
            if group in st.models:
                return group
    return fallback


__all__ = ["choice_of", "select_model", "to_messages", "to_result", "to_tools"]
