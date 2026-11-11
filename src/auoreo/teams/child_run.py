"""Running one delegated child turn (spec §16.1, §24.10, §25.11), split out of `TeamsService`.

`run_child` waits for a fleet slot, runs the turn under `ChildMonitor`, enforces the
`output_schema` / handoff schema with one retry, and records the outcome (`subagent.complete`
|`failed`, the `subagent_stop` hook, the background `<task-notification>`). A recorded
`synthetic_output` (the coordinator's structured final output) replaces the final text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import TeamMemberRow
from auoreo.runtime.hooks import subagent_stop
from auoreo.runtime.inbox import RunOptions
from auoreo.teams import merge_records
from auoreo.teams.ceiling import leaf_permissions
from auoreo.teams.format import (
    SCHEMA_MISMATCH,
    check_output,
    parse_json,
    retry_instruction,
    schema_instruction,
)
from auoreo.teams.monitor import BUDGET_EXCEEDED, ChildMonitor

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

FINAL = {"completed": "done", "max_steps": "done", "stopped": "stopped"}
Outcome = tuple[str, str, dict[str, Any] | None]


async def run_child(t: TeamsService, m: Member, prompt: str, timeout: float) -> Member:
    base = {"parent": m.parent_session_id, "child": m.session_id, "name": m.name, "agent": m.agent}
    schema = m.schema
    try:
        await t.fleet.acquire(m)
    except asyncio.CancelledError:
        await finish(t, m, "stopped", "", {"code": "CANCELLED", "message": "interrupted while queued"})
        raise
    try:
        m.status = "running"
        m.runs += 1
        await set_status(t, m, "running")
        await t.s.emit(
            m.parent_session_id, "subagent.start", {**base, "role": m.role, "subagent_id": m.subagent_id}
        )
        text = prompt + ("\n\n" + schema_instruction(schema) if schema else "")
        status, out, error = await child_turn(t, m, text, timeout)
        if schema and status == "completed" and (problem := check_output(out, schema)) is not None:
            await t.s.emit(m.parent_session_id, "subagent.retry", {**base, "error": problem})
            status, out, error = await child_turn(t, m, retry_instruction(problem, schema), timeout)
            if status == "completed" and (problem := check_output(out, schema)) is not None:
                status, error = "failed", {"code": SCHEMA_MISMATCH, "message": problem}
    except asyncio.CancelledError:
        await t.s.coordinator.cancel(m.session_id)  # the parent went away: stop the child
        await finish(t, m, "stopped", "", {"code": "CANCELLED", "message": "interrupted"})
        raise
    except Exception as exc:  # a teams bug must not leave the member "running"
        status, out, error = "failed", "", {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        t.fleet.release(m.parent_session_id)
    await finish(t, m, status, out, error)
    return m


def run_options(t: TeamsService, m: Member) -> RunOptions:
    """No memory for subagents (§16.1); leaf denials, `allowedTools` and the MCP inheritance
    filter hide tools from the list (the engine refuses them as well)."""
    deny = tuple(sorted(leaf_permissions(t.s.harness.tools))) if m.role == "leaf" else ()
    allow = tuple(m.allowed_tools) if m.allowed_tools is not None else None
    return RunOptions(
        skip_memory=True, tools_deny=deny + tuple(m.extra_deny), tools_allow=allow, exact=m.exact
    )


async def child_turn(t: TeamsService, m: Member, text: str, timeout: float) -> Outcome:
    coord = t.s.coordinator
    cap = t.s.settings.max_cost_usd
    if await coord.runner.usage.over_budget(m.session_id, cap):  # spec §25.15: root's cap
        msg = f"the session tree reached max_cost_usd={cap}; child turn blocked"
        return "failed", "", {"code": "BUDGET", "message": msg}
    async with ChildMonitor(t, m, m.budget) as monitor:
        run_id = await coord.submit(
            m.session_id,
            text,
            "followup",
            agent=m.agent,
            model=m.model,
            interactive=m.interactive,
            options=run_options(t, m),
        )
        if run_id is None:  # handled by a slash command (e.g. `/model`)
            return "completed", "", None
        try:
            await coord.wait(m.session_id, timeout)
        except TimeoutError:
            await coord.cancel(m.session_id)
            return "failed", "", {"code": "TIMEOUT", "message": f"child exceeded {timeout:.0f}s"}
    result = coord.result(run_id)
    synthetic = t.synthetic.pop(m.session_id, None)
    if monitor.verdict is not None:
        return "failed", result.text if result else "", monitor.verdict
    if result is None:
        return "stopped", "", {"code": "CANCELLED", "message": "interrupted"}
    status = "completed" if result.status in {"completed", "max_steps"} else "failed"
    text_out = json.dumps(synthetic, ensure_ascii=False) if synthetic is not None else result.text
    if monitor.budget_hit is not None:  # the grace reply is kept; the run still overran
        msg = f"delegation budget exceeded ({monitor.budget_hit})"
        return "failed", text_out, {"code": BUDGET_EXCEEDED, "message": msg}
    return status, text_out, result.error


async def finish(t: TeamsService, m: Member, status: str, text: str, error: dict[str, Any] | None) -> None:
    m.status, m.text, m.error = status, text, error
    ledger = t.s.coordinator.runner.usage
    m.usage = await ledger.totals(await ledger.session_tree(m.session_id))
    if m.handoff and status == "completed" and isinstance(value := parse_json(text), dict):
        m.handoff_data = {**value, "usage": m.usage}  # spec §25.11: usage filled by the harness
        m.text = json.dumps(m.handoff_data, ensure_ascii=False)
    await set_status(t, m, FINAL.get(status, "failed"))
    await merge_records.record_tip(t.s, m.session_id)  # M7 Wave 2: no-op unless m7.orch.merge
    data = {
        "parent": m.parent_session_id,
        "child": m.session_id,
        "subagent_id": m.subagent_id,
        "name": m.name,
        "agent": m.agent,
        "status": status,
        "usage": m.usage,
        **({"error": error} if error else {}),
        **({"handoff": m.handoff_data} if m.handoff_data else {}),
    }
    kind = "complete" if status == "completed" else "failed"
    await t.s.emit(m.parent_session_id, f"subagent.{kind}", data)
    await subagent_stop(t.s, data)
    await t.supervisor.on_finish(m)
    if m.background:
        with contextlib.suppress(Exception):  # the parent may have been deleted
            await t.inject(m.parent_session_id, m.notification())


async def set_status(t: TeamsService, m: Member, status: str) -> None:
    async with t.s.db.session() as s:
        q = select(TeamMemberRow).where(TeamMemberRow.session_id == m.session_id)
        row = (await s.execute(q)).scalar()
        if row is not None:
            row.status = status
            await s.commit()
