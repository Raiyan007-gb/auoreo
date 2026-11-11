"""Suggestion actions: what an accepted suggestion does (M7 W3 wave 2).

`prepare` runs at propose time (validation, origin stamps), `preflight` before the status
changes on accept, and `run` after it. Each is a dict dispatch on `action["type"]`; a row
whose action has no known type only records consent (as before this wave).

With `m7.agents.strict_origins` on, `prepare` stores `origin_kind` and `source` in the action
(a free JSON object on the wire) and, for an unattended origin, keeps `agent` only when it
equals the origin session's agent. `run` then never submits the turn into an unattended
origin session: it opens a new session as the origin's own agent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.suggestions.origin import ATTENDED, Origin, origin_of, strict

if TYPE_CHECKING:
    from auoreo.core.db import SuggestionRow
    from auoreo.suggestions.engine import SuggestionEngine


class ActionError(ValueError):
    """A refused action (the engine re-raises it as `SuggestionError`)."""


@dataclass(frozen=True)
class Outcome:
    session_id: str | None = None
    run_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    """Added to the `suggestion.accepted` event data (e.g. `task_id`)."""


Preparer = Callable[[Any, dict[str, Any], Origin], Awaitable[None]]
Runner = Callable[["SuggestionEngine", "SuggestionRow"], Awaitable[Outcome]]
Checker = Callable[[Any, "SuggestionRow"], Awaitable[None]]


async def prepare(services: Any, action: dict[str, Any] | None, origin_session: str | None) -> dict[str, Any]:
    """The action to store (a copy), or `ActionError`."""
    out = dict(action or {})
    kind = out.get("type")
    stamp = strict(services) or kind in STAMPED
    origin = await origin_of(services, origin_session) if stamp else Origin(ATTENDED, "session")
    if stamp:
        out["origin_kind"], out["source"] = origin.kind, origin.source
        if origin.unattended and out.get("agent") and out["agent"] != origin.agent:
            del out["agent"]  # an unattended origin never picks a wider agent
    check = PREPARERS.get(kind) if isinstance(kind, str) else None
    if check is not None:
        await check(services, out, origin)
    return out


async def preflight(services: Any, row: SuggestionRow) -> None:
    kind = (row.action or {}).get("type")
    check = PREFLIGHTS.get(kind) if isinstance(kind, str) else None
    if check is not None:
        await check(services, row)


async def run(engine: SuggestionEngine, row: SuggestionRow) -> Outcome:
    kind = (row.action or {}).get("type")
    runner = RUNNERS.get(kind) if isinstance(kind, str) else None
    return await runner(engine, row) if runner is not None else Outcome()


# ------------------------------------------------------------------ prompt
async def _prepare_prompt(services: Any, action: dict[str, Any], origin: Origin) -> None:
    if not str(action.get("text") or "").strip():
        raise ActionError("a prompt action needs non-empty `text`")


async def _run_prompt(engine: SuggestionEngine, row: SuggestionRow) -> Outcome:
    """One turn in the origin session, or in a new one when it is gone, archived or unattended
    (with `strict_origins`, also when the stored `origin_kind` says it was unattended)."""
    svc = engine.s
    action = row.action or {}
    agent = action.get("agent") or None
    sid = row.origin_session
    ses = await svc.get_session(sid) if sid else None
    stored_unattended = action.get("origin_kind", ATTENDED) != ATTENDED
    if ses is None or ses.archived or stored_unattended or await engine.is_unattended(ses.id):
        if stored_unattended and agent is None and ses is not None:
            agent = ses.agent  # the origin's own agent, never a wider one
        new = await svc.create_session(title=f"suggestion: {row.title}"[:120], agent=agent)
        sid = new.id
    assert sid is not None
    run_id = await svc.coordinator.submit(sid, str(action["text"]), agent=agent)
    return Outcome(sid, run_id)


STAMPED: frozenset[str] = frozenset()
"""Action types that always carry origin stamps (they need them for caps), flag or not."""
PREPARERS: dict[str, Preparer] = {"prompt": _prepare_prompt}
PREFLIGHTS: dict[str, Checker] = {}
RUNNERS: dict[str, Runner] = {"prompt": _run_prompt}
