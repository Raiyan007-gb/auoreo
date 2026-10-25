"""Request-time message-sequence repair (spec §8.8 alternation repair, §8.9 unanswered calls).

Applied to the outgoing request only (the persisted transcript is never rewritten), and
deterministic, so it stays prompt-cache safe:

* adjacent `user` messages merge into one (text joined by a blank line; content-part
  lists concatenate), as do adjacent plain `assistant` messages without tool calls;
* a `tool` result whose call is not in the preceding assistant message gets a minimal
  placeholder assistant tool call inserted before it;
* an assistant tool call with no result before the next non-tool message gets a
  synthetic error result.
"""

from __future__ import annotations

from typing import Any

PLACEHOLDER_TOOL = "unknown"
MISSING_RESULT = "[no result recorded for this tool call]"


def repair(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    open_ids: list[str] = []  # tool calls of the latest assistant message still unanswered
    for m in messages:
        role = m.get("role")
        if role != "tool" and open_ids:
            out.extend(_missing(open_ids))
            open_ids = []
        if role == "tool":
            call_id = str(m.get("tool_call_id") or "")
            if call_id in open_ids:
                open_ids.remove(call_id)
            elif not _answers_recent_call(out, call_id):
                out.append(_placeholder(call_id))
            out.append(m)
            continue
        prev = out[-1] if out else None
        if prev is not None and _mergeable(prev, m):
            out[-1] = {**prev, "content": _join(prev.get("content"), m.get("content"))}
            continue
        out.append(m)
        if role == "assistant":
            open_ids = [str(tc.get("id")) for tc in m.get("tool_calls") or []]
    if open_ids:
        out.extend(_missing(open_ids))
    return out


def _mergeable(prev: dict[str, Any], m: dict[str, Any]) -> bool:
    role = m.get("role")
    if role != prev.get("role") or role not in ("user", "assistant"):
        return False
    return not (prev.get("tool_calls") or m.get("tool_calls"))


def _join(a: Any, b: Any) -> Any:
    if isinstance(a, list) or isinstance(b, list):
        return [*_parts(a), *_parts(b)]
    a, b = str(a or ""), str(b or "")
    return f"{a}\n\n{b}" if a and b else a or b


def _parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return list(content)
    return [{"type": "text", "text": str(content)}] if content else []


def _answers_recent_call(out: list[dict[str, Any]], call_id: str) -> bool:
    """Whether `call_id` belongs to the assistant message that opened this tool block."""
    for m in reversed(out):
        if m.get("role") == "tool":
            continue
        return m.get("role") == "assistant" and any(
            str(tc.get("id")) == call_id for tc in m.get("tool_calls") or []
        )
    return False


def _placeholder(call_id: str) -> dict[str, Any]:
    call = {"id": call_id, "type": "function", "function": {"name": PLACEHOLDER_TOOL, "arguments": "{}"}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _missing(ids: list[str]) -> list[dict[str, Any]]:
    return [{"role": "tool", "tool_call_id": i, "content": MISSING_RESULT} for i in ids]
