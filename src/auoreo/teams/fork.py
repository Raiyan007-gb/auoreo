"""`mode: fork` seed (spec §16.2, §25.11 L2 "seeded"): the child starts with a summary of the
parent's conversation so far, contiguous from the first message, redacted at seed time and
capped at `teams2.delegation.fork_seed_max_chars` (oversize → cut with a marker)."""

from __future__ import annotations

from typing import Any

from auoreo.core.redaction import redact
from auoreo.gateway.types import text_of

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
