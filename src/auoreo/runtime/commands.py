"""Slash-command dispatch for chat input (`commands/<name>.md`, spec §2.11).

`/name rest` where `name` is a known command is either expanded into a normal chat
message (`prompt`, `plan`) or handled by the engine without a turn (`model`, `compact`).
An unknown `/word` is left alone: it may be a real message.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auoreo.harness.loader import CommandDef
from auoreo.harness.orchestrator import render

if TYPE_CHECKING:
    from auoreo.runtime.coordinator import SessionRunCoordinator
    from auoreo.runtime.runner import SessionRuntime

PLANNER_AGENT = "planner"


@dataclass
class Expanded:
    text: str
    agent: str | None = None
    handled: bool = False
    """True when the engine fully handled the command and no turn should run."""


@dataclass
class Invocation:
    coord: SessionRunCoordinator
    rt: SessionRuntime
    command: CommandDef
    args: str
    text: str
    agent: str | None


_COMMAND_RE = re.compile(r"/([a-z][a-z0-9_]*)(?:\s+(.*))?", re.DOTALL)


def parse(text: str, known: dict[str, CommandDef]) -> tuple[CommandDef, str] | None:
    """`/name rest of message` → (command, rest) when `name` is a known command."""
    m = _COMMAND_RE.fullmatch(text.strip())
    if m is None or m.group(1) not in known:
        return None
    return known[m.group(1)], (m.group(2) or "").strip()


def _render(inv: Invocation) -> str:
    template = inv.command.template or "{{args}}"
    return render(template, {"args": inv.args, "input": inv.text}).strip()


async def _prompt(inv: Invocation) -> Expanded:
    return Expanded(_render(inv) or inv.text, inv.agent)


async def _plan(inv: Invocation) -> Expanded:
    return Expanded(_render(inv) or inv.text, PLANNER_AGENT)


async def _model(inv: Invocation) -> Expanded:
    s, rt = inv.coord.s, inv.rt
    model = inv.args.split()[0] if inv.args.split() else None
    previous = rt.model
    rt.model = model
    await s.update_session(rt.session_id, model=model)
    await s.emit(rt.session_id, "session.model_changed", {"model": model, "previous": previous})
    return Expanded(inv.text, handled=True)


async def _compact(inv: Invocation) -> Expanded:
    s, sid = inv.coord.s, inv.rt.session_id
    comp = getattr(inv.coord.runner, "compaction", None)
    if comp is None:
        await s.emit(
            sid,
            "run.failed",
            {
                "run_id": f"run_{uuid.uuid4().hex[:12]}",
                "code": "UNSUPPORTED",
                "message": "compaction not available",
            },
        )
    else:
        await comp.compact(sid, trigger="manual")
    return Expanded(inv.text, handled=True)


HANDLERS: dict[str, Callable[[Invocation], Awaitable[Expanded]]] = {
    "prompt": _prompt,
    "plan": _plan,
    "model": _model,
    "compact": _compact,
}


async def expand(coord: SessionRunCoordinator, rt: SessionRuntime, text: str, agent: str | None) -> Expanded:
    parsed = parse(text, coord.s.harness.commands)
    if parsed is None:
        return Expanded(text, agent)
    command, args = parsed
    inv = Invocation(coord, rt, command, args, text, agent)
    return await HANDLERS[command.manifest.handler](inv)
