"""I4 compaction helpers (spec §11.9 S0, S2, S7): pure, shared by the service and tests.

- `meter`: S0 thresholds with `threshold_tokens` (absolute; whichever fires first wins),
  the warn `buffer`, per-model substring overrides (longest wins) and the profile floor
  (a profile ratio is raised to `profile_floor` when the window is under 512K).
- `hygiene_due`: the 85% safety net — compact before sending when the request is at least
  `hygiene_ratio` of the window even if the configured auto threshold is higher.
- `skill_prune_plan`: S2 for loaded skills — old `<skill name="x">` bodies ≥ 5000 chars
  outside the last 10 messages become `[SKILL_PRUNED: …]` markers (cap 20 per pass).
- `PostCompaction`: `notify_compaction()` listeners + `mark_post_compaction()` flags that
  the next main request carries (`metadata.post_compaction`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from auoreo.core.settings_i4 import CompactionI4Settings
from auoreo.runtime.compaction_plan import PRUNE_KEEP_RECENT, Thresholds, thresholds

log = logging.getLogger(__name__)

PROFILE_FLOOR_WINDOW = 512_000
SKILL_PRUNED = "[SKILL_PRUNED: reload with skill(name='{name}')]"
_SKILL_RE = re.compile(r'^<skill name="([^"]+)">')


def _override(cfg: CompactionI4Settings, model: str) -> tuple[float | None, int | None]:
    hits = [k for k in cfg.model_overrides if k and k in model]
    if not hits:
        return None, None
    o = cfg.model_overrides[max(hits, key=len)]
    return o.threshold_ratio, o.threshold_tokens


def meter(
    cfg: CompactionI4Settings,
    window: int,
    model: str,
    max_output: int | None,
    ratio: float | None,
) -> Thresholds:
    """S0 meter. `ratio` is an explicit `loop.compaction_threshold_ratio` (None = unset)."""
    o_ratio, o_tokens = _override(cfg, model)
    eff_ratio = o_ratio if o_ratio is not None else ratio
    if eff_ratio is None and cfg.profile_threshold_ratio is not None:
        eff_ratio = cfg.profile_threshold_ratio
        if window < PROFILE_FLOOR_WINDOW:  # raise-only floor for small windows
            eff_ratio = max(eff_ratio, cfg.profile_floor)
    th = thresholds(window, max_output, eff_ratio)
    absolute = o_tokens if o_tokens is not None else cfg.threshold_tokens
    auto = min(th.auto, absolute) if absolute is not None else th.auto
    warn = max(0, auto - min(cfg.buffer_tokens, window // 8))
    return replace(th, auto=auto, warn=warn)


def hygiene_due(cfg: CompactionI4Settings, window: int, pressure: int) -> bool:
    return window > 0 and pressure >= cfg.hygiene_ratio * window


def skill_prune_plan(
    history: list[dict[str, Any]], min_chars: int, cap: int
) -> tuple[dict[int, dict[str, Any]], int]:
    """Old, large skill bodies → reload markers. Never touches the last 10 messages."""
    changes: dict[int, dict[str, Any]] = {}
    reclaimed = 0
    for i in range(0, max(0, len(history) - PRUNE_KEEP_RECENT)):
        if len(changes) >= cap:
            break
        m = history[i]
        content = m.get("content")
        if m.get("role") != "user" or not isinstance(content, str) or len(content) < min_chars:
            continue
        match = _SKILL_RE.match(content)
        if match is None:
            continue
        marker = SKILL_PRUNED.format(name=match.group(1))
        changes[i] = {**m, "content": marker}
        reclaimed += len(content) - len(marker)
    return changes, reclaimed


Listener = Callable[[str, dict[str, Any]], Awaitable[None]]


class PostCompaction:
    """`notifyCompaction()` + `markPostCompaction()` (spec §11.9 S7)."""

    def __init__(self) -> None:
        self.listeners: list[Listener] = []
        self._marked: dict[str, str] = {}

    def subscribe(self, fn: Listener) -> None:
        self.listeners.append(fn)

    async def notify_compaction(self, session_id: str, info: dict[str, Any]) -> None:
        for fn in list(self.listeners):
            try:
                await fn(session_id, info)
            except Exception:  # a listener never fails a committed compaction
                log.exception("compaction listener failed")

    def mark_post_compaction(self, session_id: str, compaction_id: str) -> None:
        self._marked[session_id] = compaction_id

    def marks(self, session_id: str) -> dict[str, Any]:
        cid = self._marked.get(session_id)
        return {"post_compaction": cid} if cid else {}

    def clear(self, session_id: str) -> None:
        self._marked.pop(session_id, None)


__all__ = ["SKILL_PRUNED", "PostCompaction", "hygiene_due", "meter", "skill_prune_plan"]
