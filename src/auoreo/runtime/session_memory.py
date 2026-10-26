"""Session-memory writer (spec §11.9 S7): `<home>/sessions/<id>/session-memory.md`.

Deterministic (no LLM): at the end of every top-level turn one `## Turn` section is
appended with the user's request, the tools called (with their main argument), files
touched, errors seen and the outcome. The file is capped at
`i4.compaction.session_memory_chars` by dropping the oldest turn sections, so it stays a
cheap, always-current set of notes. `CompactionService` tries this file first
(session-memory compaction, no summarizer call) when a compaction is due.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.runtime.compaction_plan import is_real_user, tool_calls_in
from auoreo.runtime.compaction_restore import FILE_TOOLS, session_memory_path

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.runtime.runner import SessionRuntime, TurnResult

log = logging.getLogger(__name__)

HEADER = "# Session memory\n\nIncremental notes, one section per turn (oldest dropped first)."
TURN_MARK = "\n## Turn "


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def turn_slice(history: list[dict[str, Any]], user_text: str) -> list[dict[str, Any]]:
    """Messages of the turn that started with `user_text` (searched from the end)."""
    for i in range(len(history) - 1, -1, -1):
        m = history[i]
        content = m.get("content")
        if is_real_user(m) and isinstance(content, str) and user_text[:200] in content:
            return history[i:]
    return history[-20:]


def turn_notes(messages: list[dict[str, Any]], user_text: str, result: TurnResult, n: int) -> str:
    calls = tool_calls_in(messages)
    lines = [f"{TURN_MARK.strip()} {n} — {time.strftime('%Y-%m-%d %H:%M:%S')} ({result.status})"]
    lines.append(f"- User: {_clip(user_text, 300)}")
    if calls:
        names: dict[str, int] = {}
        for name, _, _ in calls:
            names[name] = names.get(name, 0) + 1
        lines.append("- Tools: " + ", ".join(f"{k}×{v}" if v > 1 else k for k, v in names.items()))
    files = list(dict.fromkeys(str(a["path"]) for t, a, _ in calls if t in FILE_TOOLS and a.get("path")))
    if files:
        lines.append("- Files: " + ", ".join(files[:12]))
    errors = [f"{t}: {_clip(r, 160)}" for t, _, r in calls if r and r.lower().startswith("error")]
    lines += [f"- Error: {e}" for e in errors[:3]]
    if result.error:
        lines.append(
            f"- Turn error: {result.error.get('code')}: {_clip(str(result.error.get('message', '')), 200)}"
        )
    if result.text:
        lines.append(f"- Answer: {_clip(result.text, 400)}")
    return "\n".join(lines)


def append_notes(path: Path, section: str, cap: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = HEADER
    if not text.startswith("# Session memory"):
        text = f"{HEADER}\n\n{text.strip()}" if text.strip() else HEADER
    text = f"{text.rstrip()}\n\n{section}\n"
    while len(text) > cap and text.count(TURN_MARK) > 1:  # drop the oldest turn section
        first = text.index(TURN_MARK)
        nxt = text.index(TURN_MARK, first + 1)
        text = text[:first] + text[nxt:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return text


class SessionMemoryWriter:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._turns: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return (
            self.s.settings.i4.compaction.session_memory_writer and self.s.settings.compaction.session_memory
        )

    async def record(self, rt: SessionRuntime, user_text: str, result: TurnResult) -> None:
        """Best effort; never raises (notes must not fail a turn)."""
        if not self.enabled or rt.subagent or user_text.startswith("/"):
            return
        try:
            path = session_memory_path(self.s.settings.home, rt.session_id)
            n = self._turns[rt.session_id] = self._turns.get(rt.session_id, 0) + 1
            if n == 1 and path.is_file():
                n = self._turns[rt.session_id] = path.read_text(encoding="utf-8").count(TURN_MARK) + 1
            section = turn_notes(turn_slice(rt.history, user_text), user_text, result, n)
            cap = self.s.settings.i4.compaction.session_memory_chars
            await asyncio.to_thread(append_notes, path, section, cap)
        except Exception:
            log.exception("session-memory notes failed for %s", rt.session_id)


__all__ = ["SessionMemoryWriter", "append_notes", "turn_notes", "turn_slice"]
