"""Repeated-read dedupe (M7 step 1.7, `m7.token.read_dedupe`, off by default).

A `read` that repeats an earlier `read` of the same turn (same file bytes, offset and limit)
returns a short notice in place of the content. The notice is only true while the earlier
result is still in the model's context, so it is given only when ALL of these hold:

* the file's bytes hash to the value recorded by the last full read of that path (a read that
  sees other bytes forgets every range of the path, so A → B → A is read in full);
* the state is this turn's: it lives on the `ToolContext`, which the runner builds per turn;
* the earlier result is in the session's live history, after this turn's user message, as a
  tool message with the earlier call id whose content is exactly what the handler returned.
  Compaction removes that message, pruning and the executor's redaction or truncation change
  its content, and a parallel sibling call has not persisted it yet: each means a full read.

Anything unknown (no session history, no turn message, no running call) means a full read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.tools.context import CURRENT_CALL, ToolResult

if TYPE_CHECKING:
    from auoreo.tools.context import ToolContext

NOTICE_PREFIX = "[unchanged since your earlier read]"
MIN_CHARS = 600
"""Results shorter than this are cheaper to repeat than to explain."""


@dataclass(frozen=True)
class Seen:
    call_id: str  # the tool call whose result holds the content
    digest: str  # sha256 of the text that call returned


@dataclass
class PathSeen:
    sha: str  # sha256 of the file bytes at the last full read
    ranges: dict[tuple[int, int], Seen] = field(default_factory=dict)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _enabled(ctx: ToolContext) -> bool:
    settings = getattr(ctx.services.get("services"), "settings", None)
    m7 = getattr(settings, "m7", None)
    return bool(m7 is not None and m7.token.read_dedupe)


def _history(ctx: ToolContext) -> list[dict[str, Any]] | None:
    coordinator = getattr(ctx.services.get("services"), "coordinator", None)
    lookup = getattr(coordinator, "history_of", None)
    return lookup(ctx.session_id) if callable(lookup) else None


def _in_context(ctx: ToolContext, seen: Seen) -> bool:
    """The earlier result is still what the model sees, and it belongs to this turn."""
    history, turn_msg = _history(ctx), ctx.services.get("turn_msg")
    if history is None or turn_msg is None:
        return False
    start = next((i for i, m in enumerate(history) if m is turn_msg), None)
    if start is None:
        return False
    for message in history[start + 1 :]:
        if message.get("role") == "tool" and message.get("tool_call_id") == seen.call_id:
            content = message.get("content")
            return isinstance(content, str) and _sha(content.encode()) == seen.digest
    return False


@dataclass
class Probe:
    ctx: ToolContext
    path: str
    sha: str
    span: tuple[int, int]

    def notice(self) -> ToolResult | None:
        """The short notice, or None when the content must be returned in full."""
        known = self.ctx.read_dedupe.get(self.path)
        if not isinstance(known, PathSeen):
            return None
        if known.sha != self.sha:
            del self.ctx.read_dedupe[self.path]  # the file changed: no earlier range is current
            return None
        seen = known.ranges.get(self.span)
        if seen is None:
            return None
        if not _in_context(self.ctx, seen):
            del known.ranges[self.span]
            return None
        offset, limit = self.span
        text = (
            f"{NOTICE_PREFIX} {self.path} (offset {offset}, limit {limit}) has not changed since you "
            "read it earlier in this turn. That earlier `read` result is still current and is not "
            "repeated here."
        )
        return ToolResult(text, meta={"read_dedupe": True})

    def remember(self, text: str) -> None:
        """Record a full result so a later identical read can refer to it."""
        call = CURRENT_CALL.get()
        if call is None or len(text) < MIN_CHARS:
            return
        known = self.ctx.read_dedupe.get(self.path)
        if not isinstance(known, PathSeen) or known.sha != self.sha:
            known = self.ctx.read_dedupe[self.path] = PathSeen(self.sha)
        known.ranges[self.span] = Seen(call.call_id, _sha(text.encode()))


def begin(ctx: ToolContext, path: Path, raw: bytes, offset: int, limit: int) -> Probe | None:
    """A probe for this read, or None when the feature is off (the handler then behaves as before)."""
    if not _enabled(ctx):
        return None
    return Probe(ctx, str(path), _sha(raw), (offset, limit))


__all__ = ["MIN_CHARS", "NOTICE_PREFIX", "PathSeen", "Probe", "Seen", "begin"]
