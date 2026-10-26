"""I5: a severity for every durable event (`auoreo logs --level`).

Events carry no level of their own; this maps the type (and, for tool results, the
`is_error` flag) to `debug|info|warn|error` so `logs --level warn` shows warnings and
errors only. Unknown types are `info`; high-volume streaming deltas are `debug`.
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("debug", "info", "warn", "error")
_RANK = {name: i for i, name in enumerate(LEVELS)}
_EXACT: dict[str, str] = {
    "message.delta": "debug",
    "message.reasoning": "debug",
    "tool.progress": "debug",
    "rtc.audio_level": "debug",
    "run.failed": "error",
    "run.blocked": "error",
    "error": "error",
    "run.aborted": "warn",
    "run.max_steps": "warn",
    "run.doom_loop": "warn",
    "compaction.skipped": "warn",
    "permission.requested": "warn",
}
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.(failed|crashed|error|invalid_config)$"), "error"),
    (re.compile(r"\.(denied|rejected|unknown|restarted|timed_out|stopped)$"), "warn"),
)


def level_of(event_type: str, data: dict[str, Any] | None = None) -> str:
    if event_type == "tool.completed":
        return "warn" if (data or {}).get("is_error") else "info"
    if (hit := _EXACT.get(event_type)) is not None:
        return hit
    for rx, level in _PATTERNS:
        if rx.search(event_type):
            return level
    return "info"


def at_least(event_type: str, data: dict[str, Any] | None, minimum: str) -> bool:
    return _RANK[level_of(event_type, data)] >= _RANK[minimum]
