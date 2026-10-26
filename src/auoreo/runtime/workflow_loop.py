"""Ralph-lite workflow loops (spec §9.3 L450): `steps[].loop: {until, max_iterations}`.

A looping step runs its agent turn repeatedly in the same session, re-rendering its input
with `{{loop.iteration}}` (1-based) and `{{loop.last}}` (the previous iteration's output),
until `until` holds — a regex searched in the output (multiline) or a built-in
`check:completed|nonempty|json` — or `max_iterations` is reached. A failed/blocked turn
stops the loop. Each iteration emits `workflow.loop.iteration` on the parent session.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from auoreo.harness.manifests import WorkflowLoop, WorkflowStep
from auoreo.harness.orchestrator import render
from auoreo.runtime.runner import TurnResult

OK_STATUSES = frozenset({"completed", "max_steps"})


def _json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


CHECKS: dict[str, Callable[[TurnResult], bool]] = {
    "completed": lambda r: r.status == "completed",
    "nonempty": lambda r: bool(r.text.strip()),
    "json": lambda r: _json(r.text.strip()),
}


def until_met(loop: WorkflowLoop, result: TurnResult) -> bool:
    if loop.until.startswith("check:"):
        return CHECKS[loop.until[6:]](result)
    return re.search(loop.until, result.text, re.MULTILINE) is not None


def step_input(
    step: WorkflowStep, text: str, outputs: dict[str, Any], iteration: int = 1, last: str = ""
) -> str:
    from auoreo.core.env_custom import resolve_vars
    from auoreo.core.settings import get_settings

    return render(
        resolve_vars(step.input, get_settings()),
        {"input": text, "steps": outputs, "loop": {"iteration": iteration, "last": last}},
    )


Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Turn = Callable[[str], Awaitable[TurnResult]]


async def run_looping(
    step: WorkflowStep, text: str, outputs: dict[str, Any], turn: Turn, emit: Emit
) -> TurnResult:
    """Run `step` (which has `loop`) through `turn` until its condition holds."""
    loop = step.loop
    assert loop is not None
    last, result = "", TurnResult("", "completed", "")
    for i in range(1, loop.max_iterations + 1):
        result = await turn(step_input(step, text, outputs, i, last))
        met = result.status in OK_STATUSES and until_met(loop, result)
        await emit(
            "workflow.loop.iteration",
            {
                "step": step.id,
                "iteration": i,
                "max_iterations": loop.max_iterations,
                "met": met,
                "status": result.status,
            },
        )
        if met or result.status not in OK_STATUSES:
            return result
        last = result.text
    return TurnResult(result.run_id, "max_steps", result.text, result.error)


__all__ = ["CHECKS", "run_looping", "step_input", "until_met"]
