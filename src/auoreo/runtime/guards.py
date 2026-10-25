"""Doom-loop and denial-loop guards (spec §8.9, §15 L723).

* Doom loop: the same `(tool, args, result)` `doom_loop_repeats` (3) times in a row. In an
  interactive session the user is asked through the normal approval path —
  `ask(doom_loop, patterns=[tool], always=[tool])`: `once` continues (the window re-arms),
  `always` stops checking that tool for the rest of the session, reject/timeout blocks.
  Non-interactive sessions block with `DOOM_LOOP`.
* Denial loop: `DENIAL_LIMIT` (3) consecutive permission denials within a turn stop it
  `blocked` with `DENIAL_LOOP` (any other outcome resets the tally).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.runtime.runner import AgentRunner, SessionRuntime
    from auoreo.tools.executor import ToolOutcome

DENIAL_LIMIT = 3
DENIAL_PREFIXES = (
    "permission denied (",
    "refused: ",
    "blocked by a PreToolUse hook",
    "denied by the ",
    "sandbox escalation denied",
    "user rejected this call",
)
NON_INTERACTIVE_DENIAL = re.compile(r"^'[^']+' needs (approval for |a human)")


def signature(o: ToolOutcome) -> str:
    try:
        args = json.dumps(json.loads(o.call.arguments or "{}"), sort_keys=True)
    except json.JSONDecodeError:
        args = o.call.arguments
    digest = hashlib.sha256(o.result.content.encode()).hexdigest()[:16]
    return f"{o.call.name}|{args}|{digest}"


def is_denial(o: ToolOutcome) -> bool:
    text = o.result.content
    return o.result.is_error and (
        text.startswith(DENIAL_PREFIXES) or NON_INTERACTIVE_DENIAL.match(text) is not None
    )


class LoopGuards:
    def __init__(self, runner: AgentRunner, rt: SessionRuntime, run_id: str) -> None:
        self.runner = runner
        self.rt = rt
        self.run_id = run_id
        self.recent: deque[str] = deque(maxlen=max(2, runner.s.settings.loop.doom_loop_repeats))
        self.denials = 0

    async def observe(self, outcomes: list[ToolOutcome]) -> dict[str, Any] | None:
        """The blocking error after this round, or None to continue."""
        for o in outcomes:
            self.denials = self.denials + 1 if is_denial(o) else 0
            if self.denials >= DENIAL_LIMIT:
                return {
                    "code": "DENIAL_LOOP",
                    "message": f"{DENIAL_LIMIT} consecutive permission denials in this turn",
                }
            if o.call.name not in self.rt.doom_exempt:
                self.recent.append(signature(o))
        if len(self.recent) < (self.recent.maxlen or 0) or len(set(self.recent)) != 1:
            return None
        tool = self.recent[-1].split("|", 1)[0]
        n = len(self.recent)
        self.recent.clear()  # consume-then-rearm
        if self.rt.interactive and await self._ask(tool, n):
            return None
        return {"code": "DOOM_LOOP", "message": f"identical tool call repeated {n}x with the same result"}

    async def _ask(self, tool: str, n: int) -> bool:
        s = self.runner.s
        sid = self.rt.session_id
        await s.emit(sid, "run.doom_loop", {"run_id": self.run_id, "tool": tool, "repeats": n})
        detail = f"'{tool}' was called {n}x in a row with identical arguments and result. Let it continue?"
        outcome = await s.approvals.request(sid, tool, "doom_loop", tool, detail)
        if outcome.granted and outcome.reply == "always":
            self.rt.doom_exempt.add(tool)
        return outcome.granted
