"""Doom-loop and denial-loop guards (spec §8.9, §15 L723).

* Doom loop: the same `(tool, args, result)` `doom_loop_repeats` (3) times in a row. In an
  interactive session the user is asked through the normal approval path —
  `ask(doom_loop, patterns=[tool], always=[tool])`: `once` continues (the window re-arms),
  `always` stops checking that tool for the rest of the session, reject/timeout blocks.
  Non-interactive sessions block with `DOOM_LOOP`.
* Denial loop (§19.5 L723): the tally is per SESSION (it survives across turns).
  `DENIAL_LIMIT` (3) consecutive denials stop the turn `blocked` with `DENIAL_LOOP` (any
  other outcome resets the consecutive count); `SESSION_DENIAL_LIMIT` (20) total denials
  hard-stop it with `DENIAL_LIMIT` and fall back to prompting: the smart-mode guardian is no
  longer consulted for the session, every later ask goes to the human.
* Guardian breaker (§19.3): 3 consecutive guardian DENYs stop the turn with
  `GUARDIAN_BREAKER` (typed error) instead of silently escalating.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.core import guardian

if TYPE_CHECKING:
    from auoreo.runtime.runner import AgentRunner, SessionRuntime
    from auoreo.tools.executor import ToolOutcome

DENIAL_LIMIT = 3
SESSION_DENIAL_LIMIT = 20
TALLY_KEY = "denial_tally"
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


@dataclass
class DenialTally:
    consecutive: int = 0
    total: int = 0


def tally_of(services: Any, session_id: str) -> DenialTally:
    """The session's denial tally (kept in `services.ext`, process-local)."""
    tallies = services.ext.setdefault(TALLY_KEY, {})
    found = tallies.get(session_id)
    if not isinstance(found, DenialTally):
        found = tallies[session_id] = DenialTally()
    return found


class LoopGuards:
    def __init__(self, runner: AgentRunner, rt: SessionRuntime, run_id: str) -> None:
        self.runner = runner
        self.rt = rt
        self.run_id = run_id
        self.recent: deque[str] = deque(maxlen=max(2, runner.s.settings.loop.doom_loop_repeats))
        self.tally = tally_of(runner.s, rt.session_id)

    @property
    def denials(self) -> int:
        return self.tally.consecutive

    def _denial_stop(self, o: ToolOutcome) -> dict[str, Any] | None:
        t = self.tally
        if not is_denial(o):
            t.consecutive = 0
            return None
        t.consecutive += 1
        t.total += 1
        if t.total >= SESSION_DENIAL_LIMIT:
            t.consecutive = t.total = 0
            guardian.trip(self.runner.s, self.rt.session_id)  # fall back to prompting
            msg = f"{SESSION_DENIAL_LIMIT} permission denials in this session; further asks go to a human"
            return {"code": "DENIAL_LIMIT", "message": msg}
        if t.consecutive >= DENIAL_LIMIT:
            t.consecutive = 0
            return {"code": "DENIAL_LOOP", "message": f"{DENIAL_LIMIT} consecutive permission denials"}
        return None

    async def observe(self, outcomes: list[ToolOutcome]) -> dict[str, Any] | None:
        """The blocking error after this round, or None to continue."""
        if guardian.take_breaker_stop(self.runner.s, self.rt.session_id):
            msg = f"{guardian.BREAKER_DENIES} consecutive guardian denials; further asks go to a human"
            return {"code": "GUARDIAN_BREAKER", "message": msg}
        for o in outcomes:
            if (stop := self._denial_stop(o)) is not None:
                return stop
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
