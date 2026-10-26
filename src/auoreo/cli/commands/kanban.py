"""H4: `auoreo kanban list|add|show|move|done|block` — the teams kanban board (spec §16.6).

Works in-process on `state.db` through `KanbanStore` (+ `teams.kanban_ops` for operator
moves), so it is safe next to a running server (SQLite serializes writers) and needs no
server. Events land in the same append-only log a running server streams from."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.automation.client import local
from auoreo.cli.common import console, dump_json, err_console
from auoreo.core.services import Services
from auoreo.teams import kanban_ops
from auoreo.teams.kanban import STATUSES, KanbanError, KanbanStore, task_dict

app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]
BoardOpt = Annotated[str, typer.Option("--board", "-b", help="Board name.")]
BY = "cli"


def _run[T](fn: Callable[[KanbanStore], Awaitable[T]]) -> T:
    async def go(svc: Services) -> T:
        return await fn(KanbanStore(svc))

    try:
        return local(go)
    except KanbanError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _line(t: dict[str, Any]) -> str:
    who = f" ({t['worker']})" if t.get("worker") else ""
    return f"#{t['id']} [{t['status']}{who}] {t['board']}: {t['title']}"


@app.command("list")
def list_(
    board: Annotated[str | None, typer.Option("--board", "-b")] = None,
    status: Annotated[str | None, typer.Option("--status", help=" | ".join(STATUSES))] = None,
    as_json: JsonOpt = False,
) -> None:
    """List tasks (all boards unless --board)."""
    if status is not None and status not in STATUSES:
        raise typer.BadParameter(f"--status must be one of {', '.join(STATUSES)}")

    async def go(store: KanbanStore) -> list[dict[str, Any]]:
        return [task_dict(r) for r in await store.list_tasks(board, status)]

    rows = _run(go)
    if as_json:
        dump_json(rows)
        return
    table = Table(caption=None if rows else "no tasks")
    for col in ("id", "board", "status", "worker", "failures", "title"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]), r["board"], r["status"], r["worker"] or "", str(r["failures"]), r["title"]
        )
    console.print(table)


@app.command("add")
def add(
    title: str,
    board: BoardOpt = "default",
    body: Annotated[str, typer.Option("--body", help="Task description.")] = "",
    as_json: JsonOpt = False,
) -> None:
    """Create a `ready` task."""
    if not title.strip():
        raise typer.BadParameter("title must not be empty")

    async def go(store: KanbanStore) -> dict[str, Any]:
        return task_dict(await store.create(board, title.strip(), body))

    row = _run(go)
    dump_json(row) if as_json else typer.echo(f"created {_line(row)}")


@app.command("show")
def show(task_id: int, as_json: JsonOpt = False) -> None:
    """One task with its comments."""

    async def go(store: KanbanStore) -> dict[str, Any]:
        row = task_dict(await store.get(task_id))
        row["comments"] = [
            {"author": c.author, "text": c.text, "created_at": c.created_at}
            for c in await store.comments(task_id)
        ]
        return row

    row = _run(go)
    if as_json:
        dump_json(row)
        return
    typer.echo(_line(row))
    if row["body"]:
        typer.echo(row["body"])
    for c in row["comments"]:
        typer.echo(f"  - {c['author']} @ {c['created_at']}: {c['text']}")


@app.command("move")
def move(
    task_id: int,
    to: Annotated[str | None, typer.Argument(help="ready | blocked | done")] = None,
    board: Annotated[str | None, typer.Option("--board", "-b", help="Move to this board.")] = None,
    note: Annotated[str, typer.Option("--note", help="Reason recorded as a comment.")] = "",
) -> None:
    """Move a task to another status and/or board (`ready` unblocks and resets failures)."""

    async def go(store: KanbanStore) -> dict[str, Any]:
        return task_dict(await kanban_ops.move(store, task_id, status=to, board=board, by=BY, reason=note))

    typer.echo(f"moved {_line(_run(go))}")


@app.command("done")
def done(
    task_id: int, summary: Annotated[str, typer.Option("--summary", "-m", help="Completion note.")] = ""
) -> None:
    """Complete a ready or claimed task."""

    async def go(store: KanbanStore) -> dict[str, Any]:
        return task_dict(await store.complete(task_id, None, summary))

    typer.echo(f"done {_line(_run(go))}")


@app.command("block")
def block(task_id: int, reason: Annotated[str, typer.Argument(help="Why it is blocked.")]) -> None:
    """Block a task now (a human decision; workers' failure-counted blocks go through the tool)."""

    async def go(store: KanbanStore) -> dict[str, Any]:
        return task_dict(await kanban_ops.move(store, task_id, status="blocked", by=BY, reason=reason))

    typer.echo(f"blocked {_line(_run(go))}")
