"""Computer-use safety state for the `computer` tool (spec §14.2, §24.6; I8).

Per session (process memory, never persisted):

- **input_target_mismatch** — the focused window `{pid, window_id}` seen with the last
  screenshot/state read (every mutating op returns a post-action screenshot, so it refreshes
  the baseline too). Before a mutating op the backend's `focused()` is compared with it; a
  change made outside the agent's own actions (the user switched apps, a dialog stole focus)
  refuses the op until the agent looks again.
- **sticky `pid + window_id`** — once the model names a window it stays the target of later ops
  (`opts.window`) until another window is named or `focus_app` runs.
- **element_token staleness** — tokens are valid only for the state they were read from: a
  mutating op invalidates them unless `capture_after` re-read the state; a target token not in
  the current set is refused (`stale_element_token`).
- **typeViaClipboard** — long (≥ `type_via_clipboard_chars`) or non-ASCII text is pasted:
  save clipboard → set → verify → paste key → 100 ms → restore. Backends without clipboard
  access fall back to typing.
- **turn-end unhide** — `TurnEndWatcher` (started by the I8 starter) calls `unhide()` on the
  desktop backend and releases the computer-use lock when a run of a session that used the
  tool ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

from auoreo.backends.desktop import MUTATING, OBSERVE, ActResult

log = logging.getLogger(__name__)

TERMINAL = frozenset({"run.completed", "run.failed", "run.blocked", "run.aborted", "run.max_steps"})
PASTE_DELAY_S = 0.1


@dataclass
class CuSession:
    last_focus: dict[str, Any] | None = None
    tokens: set[str] = field(default_factory=set)
    sticky: dict[str, Any] | None = None
    used: bool = False
    backend: Any = None


_STATE: dict[tuple[str, int], CuSession] = {}
"""(session_id, id(backend)) → state: a new backend instance (new Services) starts clean."""
ELEMENT_OPS = frozenset(
    {"click", "double_click", "right_click", "middle_click", "set_value", "type", "scroll", "drag"}
)


def state_for(session_id: str | None, backend: Any) -> CuSession:
    st = _STATE.setdefault((session_id or "", id(backend)), CuSession())
    st.backend = backend
    return st


def focus_key(focus: dict[str, Any] | None) -> tuple[Any, Any] | None:
    if not focus:
        return None
    return (focus.get("pid"), focus.get("window_id", focus.get("id")))


def is_token(target: str | None) -> bool:
    return bool(target) and "," not in str(target)


async def guard(st: CuSession, backend: Any, op: str, target: str | None, enabled: bool) -> str | None:
    """`[code] message` refusal before a mutating op, else None."""
    if op not in MUTATING or op == "focus_app":
        return None
    if op in ELEMENT_OPS and is_token(target) and target not in st.tokens:
        return (
            f"[stale_element_token] element {target!r} is not from the current screen state; "
            "take a screenshot (or use capture_after) and pick a fresh element"
        )
    if not enabled or st.last_focus is None:
        return None
    try:
        now = await backend.focused()
    except Exception:
        return None
    if now is not None and focus_key(now) != focus_key(st.last_focus):
        was = f"{st.last_focus.get('app')}/{st.last_focus.get('title')}"
        return (
            f"[input_target_mismatch] the focused window changed since the last screenshot "
            f"(was {was}, now {now.get('app')}/{now.get('title')}); take a screenshot before acting"
        )
    return None


def set_sticky(st: CuSession, op: str, args: dict[str, Any]) -> None:
    if op == "focus_app":
        st.sticky = None
    if args.get("window_id") is not None:
        st.sticky = {"pid": args.get("pid"), "window_id": args["window_id"]}


async def remember(st: CuSession, backend: Any, op: str, result: ActResult, capture_after: bool) -> None:
    """Refresh the focus baseline and the element-token set after an op."""
    st.used = True
    elements = result.data.get("elements")
    if op in OBSERVE or op in MUTATING:
        with contextlib.suppress(Exception):
            st.last_focus = await backend.focused()
    if isinstance(elements, list) and (op in OBSERVE or capture_after):
        st.tokens = {str(e.get("token")) for e in elements if isinstance(e, dict) and e.get("token")}
    elif op in MUTATING:
        st.tokens = set()


def wants_clipboard(text: str, threshold: int) -> bool:
    return threshold > 0 and (len(text) >= threshold or not text.isascii())


def paste_key() -> str:
    return "cmd+v" if sys.platform == "darwin" else "ctrl+v"


async def type_via_clipboard(backend: Any, text: str, mode: str, opts: dict[str, Any]) -> ActResult | None:
    """save → set → verify → paste → 100 ms → restore. None = unsupported (caller types)."""
    saved = await backend.get_clipboard()
    if saved is None or not await backend.set_clipboard(text):
        return None
    try:
        if await backend.get_clipboard() != text:
            return None
        result: ActResult = await backend.act("key", key=paste_key(), delivery_mode=mode, opts=opts)
        await asyncio.sleep(PASTE_DELAY_S)
        result.detail = f"pasted {len(text)} chars via clipboard; {result.detail}"
        return result
    finally:
        await backend.set_clipboard(saved)


class TurnEndWatcher:
    """Bus watcher: at the end of a run, `unhide()` + lock release for sessions that used CU."""

    def __init__(self, services: Any) -> None:
        self.svc = services
        self._sub: Any = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Idempotent; needs a running loop (called from the `computer` tool)."""
        if self._task is not None and not self._task.done():
            return
        self._sub = self.svc.bus.subscribe(None)
        self._task = asyncio.get_running_loop().create_task(self._watch(), name="cu:turn_end")

    async def _watch(self) -> None:
        while True:
            if self._sub.overflowed:
                self.svc.bus.unsubscribe(self._sub)
                self._sub = self.svc.bus.subscribe(None)
            ev = await self._sub.queue.get()
            if ev.type in TERMINAL:
                await self.turn_ended(str(ev.aggregate_id))

    async def turn_ended(self, session_id: str) -> bool:
        from auoreo.tools import cu_lock

        done = False
        for (sid, _), st in list(_STATE.items()):
            if sid != session_id or not st.used:
                continue
            st.used, done = False, True
            try:
                await st.backend.unhide()
            except Exception as exc:
                log.warning("computer-use: unhide failed: %s", exc)
        if done:
            cu_lock.release(self.svc.settings.home, session_id)
        return done

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._sub is not None:
            self.svc.bus.unsubscribe(self._sub)
            self._sub = None
