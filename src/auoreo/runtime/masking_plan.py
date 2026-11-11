"""Observation masking, the pure half (M7 W1 Wave 2, `m7.token.masking`).

`plan(history, cfg)` picks old, bulky tool results that a compaction run may replace with a
short placeholder (the original is spilled to disk first, `runtime.masking`). No I/O: the
token report (`core.token_report`, gate M1/M3) and the service share this function.

Selected: `role == "tool"` messages with string content of `cfg.min_chars` or more, whose
calling tool is in `PRUNABLE_TOOLS`, at or after `PRUNE_KEEP_HEAD`, not among the last
`cfg.keep_recent` tool results and not already a placeholder. Also selected: read-dedupe
notices made stale by the batch (a notice after a masked `read` result in the same turn says
"that earlier result is still current", which it no longer is).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.runtime.compaction_plan import PRUNABLE_TOOLS, PRUNE_KEEP_HEAD, is_real_user
from auoreo.tools.read_dedupe import NOTICE_PREFIX

if TYPE_CHECKING:
    from auoreo.core.settings_m7 import M7Mask

MASK_PREFIX = "[masked: "
NOTICE_MASKED = (
    "[masked: the earlier read this notice referred to was masked; read the file again if you need it]"
)
PLACEHOLDER_CHARS = 240
"""Estimated placeholder length, for the token estimates (chars // 4)."""
_SAFE = re.compile(r"[A-Za-z0-9_-]{1,96}")
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
)


@dataclass(frozen=True)
class MaskPlan:
    indices: tuple[int, ...] = ()
    """Tool results to replace by a placeholder."""
    notices: tuple[int, ...] = ()
    """Read-dedupe notices to rewrite to `NOTICE_MASKED` in the same batch."""
    tokens: int = 0
    """`M`: estimated tokens removed."""
    suffix_tokens: int = 0
    """`S`: estimated tokens from the first selected message on, once masked (the cache cost)."""

    def __bool__(self) -> bool:
        return bool(self.indices)


def tool_names(history: list[dict[str, Any]]) -> dict[str, str]:
    """`call id -> tool name` for every assistant tool call in the history."""
    return {
        str(tc.get("id")): str((tc.get("function") or {}).get("name"))
        for m in history
        if m.get("role") == "assistant"
        for tc in m.get("tool_calls") or []
    }


def spill_name(call_id: str) -> str:
    """A file stem for `call_id` that is valid on every OS: the id itself when it is short,
    plain ASCII and not a Windows reserved device name, else `h_<sha256[:16]>`."""
    if _SAFE.fullmatch(call_id) and call_id.lower() not in _RESERVED:
        return call_id
    return "h_" + hashlib.sha256(call_id.encode()).hexdigest()[:16]


def is_placeholder(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, str) and content.startswith(MASK_PREFIX)


def _size(message: dict[str, Any]) -> int:
    content = message.get("content")
    return len(content) if isinstance(content, str) else len(json.dumps(message, default=str))


def stale_notices(history: list[dict[str, Any]], reads: list[int]) -> list[int]:
    """Every read-dedupe notice after a masked `read` result, up to the next real user message."""
    out: set[int] = set()
    for i in reads:
        j = i + 1
        while j < len(history) and not is_real_user(history[j]):
            m = history[j]
            content = m.get("content")
            if m.get("role") == "tool" and isinstance(content, str) and content.startswith(NOTICE_PREFIX):
                out.add(j)
            j += 1
    return sorted(out)


def plan(history: list[dict[str, Any]], cfg: M7Mask) -> MaskPlan:
    names = tool_names(history)
    tools = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    recent = set(tools[len(tools) - cfg.keep_recent :]) if cfg.keep_recent else set()
    chosen: list[int] = []
    for i in tools:
        m = history[i]
        content, call_id = m.get("content"), m.get("tool_call_id")
        if i < PRUNE_KEEP_HEAD or i in recent or not isinstance(call_id, str):
            continue
        if not isinstance(content, str) or len(content) < cfg.min_chars or content.startswith(MASK_PREFIX):
            continue
        if names.get(call_id) in PRUNABLE_TOOLS:
            chosen.append(i)
    if not chosen:
        return MaskPlan()
    reads = [i for i in chosen if names.get(str(history[i].get("tool_call_id"))) == "read"]
    removed = sum(max(0, _size(history[i]) - PLACEHOLDER_CHARS) for i in chosen) // 4
    suffix = max(0, sum(_size(m) for m in history[chosen[0] :]) // 4 - removed)
    return MaskPlan(tuple(chosen), tuple(stale_notices(history, reads)), removed, suffix)


__all__ = [
    "MASK_PREFIX",
    "NOTICE_MASKED",
    "MaskPlan",
    "is_placeholder",
    "plan",
    "spill_name",
    "stale_notices",
    "tool_names",
]
