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
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from auoreo.core.db import SuggestionRow, utcnow
from auoreo.suggestions.origin import ATTENDED, Origin, origin_of, strict

if TYPE_CHECKING:
    from auoreo.suggestions.engine import SuggestionEngine

KB_PREFIX = "kb:"
"""Dedupe-key prefix of `kanban_create` suggestions: `kb:<source>:<model key>`."""
KEY_MAX = 128


class ActionError(ValueError):
    """A refused action (the engine re-raises it as `SuggestionError`)."""


@dataclass(frozen=True)
class Outcome:
    session_id: str | None = None
    run_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    """Added to the `suggestion.accepted` event data (e.g. `task_id`)."""


Preparer = Callable[[Any, dict[str, Any], Origin], Awaitable[dict[str, Any] | None]]
Runner = Callable[["SuggestionEngine", SuggestionRow], Awaitable[Outcome]]
Checker = Callable[[Any, SuggestionRow], Awaitable[None]]
Admitter = Callable[[Any, dict[str, Any], str | None], Awaitable[tuple[str | None, SuggestionRow | None]]]


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
        out = await check(services, out, origin) or out
    return out


async def admit(
    services: Any, action: dict[str, Any], dedupe_key: str | None
) -> tuple[str | None, SuggestionRow | None]:
    """Under the engine lock: (the key to store, an existing row to return instead). Default:
    a pending row with the same key is returned (pending-only dedupe)."""
    kind = action.get("type")
    custom = ADMITTERS.get(kind) if isinstance(kind, str) else None
    if custom is not None:
        return await custom(services, action, dedupe_key)
    if not dedupe_key:
        return None, None
    key = dedupe_key[:KEY_MAX]
    if key.startswith(KB_PREFIX) and services.settings.m7.agents.kanban_suggestions:
        raise ActionError(f"the '{KB_PREFIX}' dedupe_key prefix is reserved for kanban suggestions")
    async with services.db.session() as s:
        q = select(SuggestionRow).where(SuggestionRow.dedupe_key == key, SuggestionRow.status == "pending")
        return key, (await s.execute(q.limit(1))).scalar()


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
    return None


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


# ----------------------------------------------------------- kanban_create
TITLE_MAX, BODY_MAX, REF_MAX = 200, 8000, 2000


def _allowed(services: Any, board: str, profile: str) -> None:
    """The flag, the board and profile allowlists, and a known agent (propose and accept)."""
    agents = services.settings.m7.agents
    if not agents.kanban_suggestions:
        raise ActionError("kanban suggestions are disabled (m7.agents.kanban_suggestions)")
    if board not in agents.triage.boards:
        raise ActionError(f"board '{board}' is not in m7.agents.triage.boards")
    if profile not in agents.triage.profiles:
        raise ActionError(f"profile '{profile}' is not in m7.agents.triage.profiles")
    if profile not in services.harness.agents:
        raise ActionError(f"unknown agent '{profile}' (profile)")


async def _prepare_kanban(services: Any, action: dict[str, Any], origin: Origin) -> dict[str, Any]:
    """Keep only the known keys plus the origin stamps; check sizes and allowlists."""
    board = str(action.get("board") or "")
    profile = str(action.get("profile") or services.settings.i3.kanban.agent)
    title = str(action.get("title") or "").strip()
    body = str(action.get("body") or "")
    ref = str(action.get("ref") or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise ActionError(f"a kanban suggestion needs a title of 1..{TITLE_MAX} characters")
    if len(body) > BODY_MAX:
        raise ActionError(f"a kanban suggestion body is at most {BODY_MAX} characters")
    if len(ref) > REF_MAX:
        raise ActionError(f"a kanban suggestion ref is at most {REF_MAX} characters")
    _allowed(services, board, profile)
    out = {"type": "kanban_create", "board": board, "title": title, "body": body, "profile": profile}
    if ref:
        out["ref"] = ref
    return out | {"origin_kind": action["origin_kind"], "source": action["source"]}


async def _admit_kanban(
    services: Any, action: dict[str, Any], dedupe_key: str | None
) -> tuple[str | None, SuggestionRow | None]:
    """`kb:<source>:<key>`; dedupe over pending, accepted and denied rows of the last
    `dedupe_days`; per-source pending and daily caps (refused and audited `suggestion.capped`)."""
    triage = services.settings.m7.agents.triage
    if not (dedupe_key or "").strip():
        raise ActionError("a kanban suggestion needs a dedupe_key (e.g. '<repo>#<issue>')")
    prefix = f"{KB_PREFIX}{action['source']}:"
    key = f"{prefix}{dedupe_key}"[:KEY_MAX]
    now = utcnow()
    async with services.db.session() as s:
        q = select(SuggestionRow).where(
            SuggestionRow.dedupe_key == key,
            SuggestionRow.status.in_(("pending", "accepted", "denied")),
            SuggestionRow.created_at >= now - timedelta(days=triage.dedupe_days),
        )
        existing = (await s.execute(q.order_by(SuggestionRow.id.desc()).limit(1))).scalar()
        if existing is not None:
            return key, existing
        mine = SuggestionRow.dedupe_key.startswith(prefix, autoescape=True)
        count = select(func.count()).select_from(SuggestionRow).where(mine)
        pending = (await s.execute(count.where(SuggestionRow.status == "pending"))).scalar_one()
        day = count.where(SuggestionRow.created_at >= now - timedelta(days=1))
        daily = (await s.execute(day)).scalar_one()
    for name, used, limit in (
        ("per_source_pending", pending, triage.per_source_pending),
        ("per_source_daily", daily, triage.per_source_daily),
    ):
        if used >= limit:
            await services.audit.record(
                actor="automation",
                action="suggestion.capped",
                decision="deny",
                reason=name,
                detail={"source": action["source"], "cap": name, "limit": limit},
            )
            raise ActionError(
                f"source {action['source']} has reached m7.agents.triage.{name} ({limit}); "
                "wait for the owner to decide its pending suggestions"
            )
    return key, None


async def _preflight_kanban(services: Any, row: SuggestionRow) -> None:
    action = row.action or {}
    _allowed(services, str(action.get("board") or ""), str(action.get("profile") or ""))
    if services.ext.get("teams") is None:
        raise ActionError("kanban suggestions need the teams extension (it is not running)")


async def _run_kanban(engine: SuggestionEngine, row: SuggestionRow) -> Outcome:
    from auoreo.automation.templating import untrusted

    svc = engine.s
    action = row.action or {}
    source = str(action.get("source") or "session")
    kb = svc.ext["teams"].kanban
    header = (
        f"Kanban task from accepted suggestion #{row.id} (source {source}). The owner accepted "
        "the title; the description below was written by that source."
    )
    body = f"{header}\n\n{untrusted(source, str(action.get('body') or ''))}"
    task = await kb.create(action["board"], action["title"], body, profile=action.get("profile"))
    if action.get("ref"):
        await kb.attach(task.id, action["ref"], note=f"dedupe={row.dedupe_key}", by="suggestion")
    await svc.audit.record(
        actor="user",
        action="suggestion.kanban_created",
        decision="allow",
        reason="explicit consent",
        detail={"suggestion_id": row.id, "task_id": task.id, "board": task.board, "source": source},
    )
    return Outcome(extra={"task_id": task.id})


STAMPED: frozenset[str] = frozenset({"kanban_create"})
"""Action types that always carry origin stamps (their caps need the source), flag or not."""
PREPARERS: dict[str, Preparer] = {"prompt": _prepare_prompt, "kanban_create": _prepare_kanban}
ADMITTERS: dict[str, Admitter] = {"kanban_create": _admit_kanban}
PREFLIGHTS: dict[str, Checker] = {"kanban_create": _preflight_kanban}
RUNNERS: dict[str, Runner] = {"prompt": _run_prompt, "kanban_create": _run_kanban}
