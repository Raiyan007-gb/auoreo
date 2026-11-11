"""Kanban board hard boundary for workers (spec §16.6, §24.10, §25.11).

A *worker* is a session the dispatcher spawned for a board (`KanbanStore.worker_boards`), or
any session of a process started with `AUOREO_KANBAN_BOARD=<board>` (a worker process; the
variable can never come from a project `.env`). For a worker:

- `board` is not a free argument: tools act on the pinned board; naming another one fails;
- tasks on other boards are refused (cross-board reads denied);
- `kanban_list` / `kanban_unblock` (orchestrator verbs) are refused.

The checks run at call time in every kanban handler, so `tool_call` indirection or a tool
list that still shows the verb cannot get around them.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError

if TYPE_CHECKING:
    from auoreo.core.db import KanbanTaskRow
    from auoreo.teams.kanban import KanbanStore

ENV = "AUOREO_KANBAN_BOARD"
ORCHESTRATOR_VERBS = ("list", "unblock")


def pinned_board(ctx: ToolContext) -> str | None:
    env = os.environ.get(ENV, "").strip()
    if env:
        return env
    return teams_for(ctx).kanban.worker_boards.get(ctx.session_id)


def board_arg(ctx: ToolContext, args: dict[str, Any]) -> str:
    """The board a call acts on: the pinned one for a worker (another name is refused)."""
    pinned = pinned_board(ctx)
    asked = args.get("board")
    if pinned is None:
        return str(asked or "default")
    if asked and asked != pinned:
        raise ToolError(f"kanban workers are confined to board '{pinned}' ({ENV}); '{asked}' is off limits")
    return pinned


async def task_on_board(ctx: ToolContext, task_id: int) -> tuple[KanbanStore, KanbanTaskRow]:
    """(store, task) after checking a worker may touch that task."""
    kb = teams_for(ctx).kanban
    row = await attempt(kb.get(task_id))
    pinned = pinned_board(ctx)
    if pinned is not None and row.board != pinned:
        raise ToolError(f"task #{task_id} is on another board; kanban workers are confined to '{pinned}'")
    return kb, row


def refuse_worker(ctx: ToolContext, verb: str) -> None:
    if verb in ORCHESTRATOR_VERBS and pinned_board(ctx) is not None:
        raise ToolError(f"kanban_{verb} is for orchestrators; kanban workers cannot {verb} tasks")


async def refuse_unattended(ctx: ToolContext) -> None:
    """M7 W3 wave 2: with `m7.agents.strict_origins`, a job, webhook or connector session may
    not create kanban tasks directly (a task can start a worker run with no human step); it
    proposes a `kanban_create` suggestion instead. Workers and attended sessions are unaffected."""
    from auoreo.suggestions.origin import origin_of, strict

    svc = ctx.services.get("services")
    if svc is None or not strict(svc):
        return
    hint = ctx.services.get("unattended")
    kind = hint if hint in ("cron", "platform") else (await origin_of(svc, ctx.session_id)).kind
    if kind in ("cron", "platform"):
        raise ToolError(
            f"kanban_create is not available to unattended {kind} sessions; propose the task "
            "with suggest(kanban={board, title, body, ...}) and a dedupe_key so a human can accept it"
        )
