"""Ralph-lite workflow loops (spec §9.3 L450): `steps[].loop: {until, max_iterations}`.

A looping step runs its agent turn repeatedly in the same session, re-rendering its input
with `{{loop.iteration}}` (1-based) and `{{loop.last}}` (the previous iteration's output),
until `until` holds — a regex searched in the output (multiline) or a built-in
`check:completed|nonempty|json|files_exist:<paths>` — or `max_iterations` is reached.
`files_exist` (M7) holds when every listed relative path exists inside the session's
workspace root (never outside it; no workspace known = not met). A failed/blocked turn
stops the loop. Each iteration emits `workflow.loop.iteration` on the parent session.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
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


def _files_exist(arg: str, cwd: Path | None) -> bool:
    if cwd is None:
        return False
    root = cwd.resolve()
    for rel in arg.split(","):
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.exists():
            return False
    return True


Check = Callable[[TurnResult, str, "Path | None"], bool]
CHECKS: dict[str, Check] = {
    "completed": lambda r, _a, _c: r.status == "completed",
    "nonempty": lambda r, _a, _c: bool(r.text.strip()),
    "json": lambda r, _a, _c: _json(r.text.strip()),
    "files_exist": lambda _r, arg, cwd: _files_exist(arg, cwd),
}


def until_met(loop: WorkflowLoop, result: TurnResult, cwd: Path | None = None) -> bool:
    if loop.until.startswith("check:"):
        name, _, arg = loop.until[6:].partition(":")
        return CHECKS[name](result, arg, cwd)
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
    step: WorkflowStep, text: str, outputs: dict[str, Any], turn: Turn, emit: Emit, cwd: Path | None = None
) -> TurnResult:
    """Run `step` (which has `loop`) through `turn` until its condition holds; `cwd` is the
    session's workspace root (for `check:files_exist`)."""
    loop = step.loop
    assert loop is not None
    last, result = "", TurnResult("", "completed", "")
    for i in range(1, loop.max_iterations + 1):
        result = await turn(step_input(step, text, outputs, i, last))
        met = result.status in OK_STATUSES and until_met(loop, result, cwd)
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
