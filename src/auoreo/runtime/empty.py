"""Empty-reply classes (spec §25.6 L969).

A reply with no usable output is classified instead of getting one generic nudge:

| class               | what came back                                  | recovery                     |
|---------------------|-------------------------------------------------|------------------------------|
| `truly_empty`       | nothing                                         | 1 nudge                      |
| `whitespace`        | only whitespace text                            | 1 nudge                      |
| `thinking_only`     | reasoning, no answer and no tool call           | answer-prefill retry ×N (2)  |
| `tool_call_invalid` | only tool calls, all malformed (no name / JSON) | 1 "re-issue the call" nudge  |
| `refusal`           | a refusal / content-filter stop with no text    | none (deterministic)         |

Still empty after its recovery → the step is terminal-empty: the assistant row is persisted as
the `(empty)` sentinel (never the user-facing answer) and the turn fails `EMPTY_RESPONSE`.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from auoreo.runtime.budget import OUT_OF_BAND

EmptyClass = Literal["truly_empty", "whitespace", "thinking_only", "tool_call_invalid", "refusal"]
EMPTY_SENTINEL = "(empty)"
REFUSAL_FINISHES = frozenset({"refusal", "content_filter"})
_REPLY = "Reply now: call a tool or give your answer."
NOTES: dict[str, str] = {
    "truly_empty": f"{OUT_OF_BAND} Your previous reply was empty. {_REPLY}",
    "whitespace": f"{OUT_OF_BAND} Your previous reply was blank. {_REPLY}",
    "thinking_only": (
        f"{OUT_OF_BAND} You finished thinking but did not answer. Do not think again: write your "
        "answer (or the tool call) now."
    ),
    "tool_call_invalid": (
        f"{OUT_OF_BAND} Your tool call was malformed (missing name or arguments that are not a JSON "
        "object). Re-issue it with valid JSON arguments."
    ),
}


def valid_call(call: Any) -> bool:
    if not getattr(call, "name", ""):
        return False
    try:
        return isinstance(json.loads(getattr(call, "arguments", "") or "{}"), dict)
    except (TypeError, ValueError):
        return False


def classify(text: str, calls: list[Any], reasoning: str, finish: str | None) -> EmptyClass | None:
    """None when the reply is usable (or a length cut, which has its own recovery)."""
    if calls:
        return None if any(valid_call(c) for c in calls) or text.strip() else "tool_call_invalid"
    if text.strip():
        return None
    if finish == "length":
        return None
    if finish in REFUSAL_FINISHES:
        return "refusal"
    if reasoning.strip():
        return "thinking_only"
    return "whitespace" if text else "truly_empty"


def attempts(kind: EmptyClass, thinking_retries: int) -> int:
    """How many recovery calls a class gets (refusals are deterministic: none)."""
    return {"thinking_only": thinking_retries, "refusal": 0}.get(kind, 1)


__all__ = ["EMPTY_SENTINEL", "NOTES", "EmptyClass", "attempts", "classify", "valid_call"]
