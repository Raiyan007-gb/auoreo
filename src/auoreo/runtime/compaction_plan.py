"""Pure planning helpers for compaction (spec §11.9): meter, split, prune, transcript.

No I/O and no services: everything here is a function of the history list, so the
service in `runtime.compaction` and the tests share one implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PRUNABLE_TOOLS = frozenset(
    {
        # shell
        "bash",
        "pwsh",
        "shell",
        "exec_command",
        # read-type
        "read",
        "grep",
        "glob",
        "codebase_map",
        # web
        "web_fetch",
        "web_search",
        # edit/write (their results echo diffs or file bodies)
        "edit",
        "write",
        "str_replace",
        "apply_patch",
    }
)
PRUNE_MIN_CHARS = 8000
PRUNE_HEAD = 4096
PRUNE_TAIL = 1024
PRUNE_KEEP_RECENT = 10
PRUNE_KEEP_HEAD = 3
"""S2 head protection: the system prompt (never in history) + the first 3 messages."""
PRUNE_MIN_RECLAIM_TOKENS = 4096
TAIL_MIN_MESSAGES = 20
SUMMARY_INPUT_CHARS = 160_000
SUMMARY_TAG = "<compaction-summary"
TRUNCATED_MARKER = "[earlier conversation dropped to fit the summarizer's context window]"


@dataclass(frozen=True)
class Thresholds:
    window: int
    effective: int
    auto: int
    blocking: int
    warn: int = 0
    """`auto − buffer` (I4 S0): the pressure where a one-time warning is emitted."""


def thresholds(window: int, max_output: int | None = None, ratio: float | None = None) -> Thresholds:
    """`effective = window − min(max_output, 20k)`, `auto = effective − 13k`,
    `blocking = effective − 3k`. Reserves are capped at a fraction of the window so tiny
    windows (tests, local models) still get positive thresholds; unchanged for ≥ 104k."""
    reserve = min(max_output or 8192, 20_000, window // 4)
    effective = window - reserve
    auto = int(window * ratio) if ratio is not None else effective - min(13_000, window // 8)
    blocking = effective - min(3_000, window // 16)
    return Thresholds(window, effective, auto, blocking, max(0, auto - min(20_000, window // 8)))


def is_real_user(m: dict[str, Any]) -> bool:
    content = m.get("content")
    return m.get("role") == "user" and not (isinstance(content, str) and content.startswith(SUMMARY_TAG))


def turn_index(history: list[dict[str, Any]], turn_msg: dict[str, Any]) -> int:
    """S7: index of this turn's user message, searched from the end by identity (indices
    move when compaction replaces the middle). Falls back to the last real user message."""
    for i in range(len(history) - 1, -1, -1):
        if history[i] is turn_msg:
            return i
    return next((i for i in range(len(history) - 1, -1, -1) if is_real_user(history[i])), -1)


def head_end_of(history: list[dict[str, Any]]) -> int:
    """S6 head: everything through the first user message."""
    return next((i + 1 for i, m in enumerate(history) if m.get("role") == "user"), 0)


def _back_to_user(history: list[dict[str, Any]], start: int, floor: int) -> int:
    n = len(history)
    while start > floor and (start >= n or not is_real_user(history[start])):
        start -= 1
    return start


def split_points(history: list[dict[str, Any]], sizes: list[int], retain_ratio: float) -> tuple[int, int]:
    """Return `(head_end, tail_start)`; the middle `history[head_end:tail_start]` is replaced.

    Head = through the first user message. Tail = last ≥ TAIL_MIN_MESSAGES messages holding
    ≥ retain_ratio of the tokens, extended back so it starts at a real user message (which
    also guarantees no tool_call/result pair is split)."""
    n = len(history)
    head_end = head_end_of(history)
    start = max(head_end, n - (TAIL_MIN_MESSAGES if retain_ratio > 0 else 0))
    if retain_ratio > 0:
        target = retain_ratio * sum(sizes)
        kept = sum(sizes[start:])
        while start > head_end and kept < target:
            start -= 1
            kept += sizes[start]
    return head_end, _back_to_user(history, start, head_end)


def session_memory_split(
    history: list[dict[str, Any]], sizes: list[int], min_tokens: int, min_messages: int, max_tokens: int
) -> tuple[int, int]:
    """S7 session-memory compaction keeps a recent tail of ≥ `min_tokens` and ≥ `min_messages`
    text messages, capped at `max_tokens`, starting at a real user message."""
    head_end = head_end_of(history)
    start, kept, texts = len(history), 0, 0
    while start > head_end and (kept < min_tokens or texts < min_messages):
        if kept + sizes[start - 1] > max_tokens:
            break
        start -= 1
        kept += sizes[start]
        msg = history[start]
        texts += int(isinstance(msg.get("content"), str) and msg.get("role") != "tool")
    return head_end, _back_to_user(history, start, head_end)


def partial_bounds(
    history: list[dict[str, Any]], default_tail: int, up_to: int | None, from_: int | None
) -> tuple[int, int]:
    """S4(d) manual partial compaction. `up_to` = replace older messages before that index
    (keep everything after; the cache-friendly direction); `from` = replace messages from
    that index to the end (keeps the earlier prefix byte-identical). The head is always
    protected and bounds move so no tool_call/result pair is split."""
    n = len(history)
    head_end = head_end_of(history)
    start = max(head_end, from_ if from_ is not None else head_end)
    end = up_to if up_to is not None else (n if from_ is not None else default_tail)
    end = min(max(end, 0), n)
    while head_end < start < n and history[start].get("role") == "tool":
        start -= 1
    while end < n and history[end].get("role") == "tool":
        end += 1
    return start, end


def prune_plan(history: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], int]:
    """S2: old bulky shell/read/web/edit tool results → head + marker + tail.
    Never touches the first PRUNE_KEEP_HEAD or the last PRUNE_KEEP_RECENT messages."""
    names = {
        tc.get("id"): (tc.get("function") or {}).get("name")
        for m in history
        if m.get("role") == "assistant"
        for tc in m.get("tool_calls") or []
    }
    changes: dict[int, dict[str, Any]] = {}
    reclaimed = 0
    for i in range(PRUNE_KEEP_HEAD, max(0, len(history) - PRUNE_KEEP_RECENT)):
        m = history[i]
        content = m.get("content")
        if m.get("role") != "tool" or not isinstance(content, str) or len(content) <= PRUNE_MIN_CHARS:
            continue
        if names.get(m.get("tool_call_id")) not in PRUNABLE_TOOLS:
            continue
        cut = len(content) - PRUNE_HEAD - PRUNE_TAIL
        new = f"{content[:PRUNE_HEAD]}\n… [pruned {cut} chars] …\n{content[-PRUNE_TAIL:]}"
        changes[i] = {**m, "content": new}
        reclaimed += len(content) - len(new)
    return changes, reclaimed


def round_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """API-round groups: a new group starts at every user or assistant message; tool results
    stay with the assistant message that called them."""
    groups: list[list[dict[str, Any]]] = []
    for m in messages:
        if m.get("role") == "tool" and groups:
            groups[-1].append(m)
        else:
            groups.append([m])
    return groups


def drop_oldest_group(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """S5 PTL retry: drop the oldest API-round group; a synthetic user marker leads when the
    remainder would start with an assistant message. None when nothing is left to drop."""
    groups = round_groups([m for m in messages if m.get("content") != TRUNCATED_MARKER])
    if len(groups) <= 1:
        return None
    rest = [m for g in groups[1:] for m in g]
    if rest[0].get("role") != "user":
        rest.insert(0, {"role": "user", "content": TRUNCATED_MARKER})
    return rest


def _parts_text(content: Any) -> str:
    """Flatten list content for the summarizer; images become short placeholders, never base64."""
    if not content:
        return ""
    if not isinstance(content, list):
        return json.dumps(content)
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            out.append(str(part))
        elif part.get("type") == "text":
            out.append(str(part.get("text", "")))
        elif part.get("type") == "image_url":
            out.append("[image]")
        else:
            out.append(json.dumps(part)[:200])
    return "\n".join(out)


def text_only(m: dict[str, Any]) -> dict[str, Any]:
    """A message with images replaced by `[image]` markers (verbatim replay to the summarizer)."""
    content = m.get("content")
    return m if content is None or isinstance(content, str) else {**m, "content": _parts_text(content)}


def render_transcript(messages: list[dict[str, Any]], cap: int = SUMMARY_INPUT_CHARS) -> str:
    """Plain-text transcript for the summarizer; oldest entries are dropped past `cap`."""
    entries: list[str] = []
    for m in messages:
        content = m.get("content")
        text = content if isinstance(content, str) else _parts_text(content)
        if m.get("role") == "tool":
            entries.append(f"[tool result {m.get('tool_call_id', '')}]\n{text}")
            continue
        entry = f"[{m.get('role')}]\n{text}".rstrip()
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            entry += f"\n-> {fn.get('name')}({str(fn.get('arguments', ''))[:2000]})"
        entries.append(entry)
    total = sum(len(e) + 2 for e in entries)
    first = 0
    while first < len(entries) - 1 and total > cap:
        total -= len(entries[first]) + 2
        first += 1
    kept = entries[first:]
    if kept and len(kept[0]) > cap:
        kept[0] = kept[0][-cap:]
    note = [f"[... {first} older messages omitted ...]"] if first else []
    return "\n\n".join(note + kept)


def tool_calls_in(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str | None]]:
    """`(tool name, parsed arguments, result text)` for every call in `messages`, in order."""
    results = {m.get("tool_call_id"): m.get("content") for m in messages if m.get("role") == "tool"}
    out: list[tuple[str, dict[str, Any], str | None]] = []
    for m in messages:
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        for tc in calls or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            res = results.get(tc.get("id"))
            text = res if isinstance(res, str) else None
            out.append((str(fn.get("name", "")), args if isinstance(args, dict) else {}, text))
    return out


__all__ = [
    "PRUNABLE_TOOLS",
    "SUMMARY_TAG",
    "Thresholds",
    "drop_oldest_group",
    "head_end_of",
    "is_real_user",
    "partial_bounds",
    "prune_plan",
    "render_transcript",
    "round_groups",
    "session_memory_split",
    "split_points",
    "text_only",
    "thresholds",
    "tool_calls_in",
    "turn_index",
]
