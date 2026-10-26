"""Fork seeds (spec §16.2, §24.10, §25.11 isolation levels).

- `mode: fork` (L2 "seeded"): the child starts with a summary of the parent's conversation so
  far, contiguous from the first message, redacted at seed time and capped at
  `teams2.delegation.fork_seed_max_chars` (oversize → cut with a marker).
- `mode: exact` (L3 "exact-fork", `i3.delegation.fork_cache_exact`): the child inherits the
  parent's full history, its byte-identical system prompt and its exact tool list
  (`ExactSpec`), with `model=inherit`, so the provider's prompt cache hits on the shared
  prefix. Open tool calls get placeholder results; a fork directive is the recursion guard.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from auoreo.core.redaction import redact
from auoreo.gateway.types import text_of
from auoreo.runtime.compaction_plan import turn_index

PER_MESSAGE_CHARS = 4_000
_ROLES = {"user": "user", "assistant": "assistant", "tool": "tool result"}


def _line(msg: dict[str, Any]) -> str | None:
    role = _ROLES.get(str(msg.get("role")))
    if role is None:
        return None
    text = text_of(msg.get("content")).strip()
    calls = msg.get("tool_calls") or []
    if calls:
        names = ", ".join(str((c.get("function") or {}).get("name") or c.get("name") or "?") for c in calls)
        text = (text + f"\n[called tools: {names}]").strip()
    if not text:
        return None
    if len(text) > PER_MESSAGE_CHARS:
        text = text[:PER_MESSAGE_CHARS] + f"… [{len(text) - PER_MESSAGE_CHARS} chars cut]"
    return f"[{role}] {text}"


def fork_seed(history: list[dict[str, Any]], cap: int) -> str:
    """The parent-context block a forked child sees before its goal ('' when there is none)."""
    lines = [line for msg in history if (line := _line(msg)) is not None]
    if not lines:
        return ""
    body, used, cut = [], 0, False
    for line in lines:
        if used + len(line) > cap:
            cut = True
            break
        body.append(line)
        used += len(line) + 1
    text = redact("\n".join(body))
    omitted = len(lines) - len(body)
    marker = f"\n… [parent context cut at {cap} chars; {omitted} messages omitted]" if cut else ""
    return (
        "You are a forked subagent. The parent's conversation so far (summary, read-only):\n"
        f"<parent-context>\n{text}{marker}\n</parent-context>\n\n"
    )


# --------------------------------------------------------------------------- I3: exact fork
PLACEHOLDER = "[forked: this call continues in the parent; you are the fork, carry on below]"
FORK_DIRECTIVE = (
    "<fork-directive>\nYou are a forked worker of the conversation above (same instructions, "
    "same tools). Do ONLY the task below, then answer with the result. Do not delegate or fork "
    "again.\n</fork-directive>\n\n"
)


@dataclass(frozen=True)
class ExactSpec:
    """What an exact fork reuses from the parent's running turn, byte for byte: the frozen
    `AgentSpec` (system prompt, rules, variant) and the exact tool list sent to the model."""

    spec: Any
    tools: tuple[Any, ...]


@dataclass(frozen=True)
class FrozenTurn:
    """The parent's current turn as `delegate_task` sees it (from `ToolContext.services`)."""

    spec: Any
    options: Any
    context: str
    turn_msg: Any


def cache_key(system_prompt: str, tool_schemas: list[dict[str, Any]], history: list[dict[str, Any]]) -> str:
    """sha256 over (system + tools + history): equal keys ⇒ the provider sees the same prefix."""
    blob = json.dumps([system_prompt, tool_schemas, history], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def exact_history(history: list[dict[str, Any]], turn_msg: Any, context: str) -> list[dict[str, Any]]:
    """The parent's history as the model saw it in the current step: this turn's user message
    carries the `<context>` block (as `AgentRunner._request` renders it) and every still-open
    tool call of the trailing assistant message gets a placeholder result."""
    out: list[dict[str, Any]] = []
    idx = turn_index(history, turn_msg) if turn_msg is not None else -1
    for i, m in enumerate(history):
        if i == idx and context:
            m = {**m, "content": f"<context>\n{context}\n</context>\n\n{m['content']}"}
        out.append(dict(m))
    answered = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    last = next((m for m in reversed(out) if m.get("role") == "assistant"), None)
    for call in (last or {}).get("tool_calls") or []:
        if call.get("id") not in answered:
            out.append({"role": "tool", "tool_call_id": call.get("id"), "content": PLACEHOLDER})
    return out
