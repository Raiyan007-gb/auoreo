from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, with_services
from auoreo.core import sessions_ops as ops
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_(
    limit: int = 20,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    archived: Annotated[bool, typer.Option("--archived", help="Include archived sessions.")] = False,
) -> None:
    """List recent sessions."""

    async def go(svc: Services) -> list[dict[str, str]]:
        return [
            {
                "id": r.id,
                "title": r.title,
                "agent": r.agent,
                "status": r.status,
                "updated": r.updated_at.isoformat(),
            }
            for r in await svc.list_sessions(limit, include_archived=archived)
        ]

    from auoreo.cli.common import settings
    from auoreo.remote import cli_client, uds  # --- W2-B: a live daemon socket wins

    sock = uds.daemon_socket(settings().home)
    rows = cli_client.list_sessions(sock, limit, archived) if sock else with_services(go, memory=False)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("id", "title", "agent", "status", "updated"):
        table.add_column(col)
    for r in rows:
        table.add_row(*(r[c] for c in ("id", "title", "agent", "status", "updated")))
    console.print(table)


def _run_op[T](fn: Callable[[Services], Awaitable[T]]) -> T:
    """Run a session op; unknown id / running session become a clean exit 1."""
    try:
        return with_services(fn, memory=False)
    except KeyError as exc:
        err_console.print(f"[red]{str(exc).strip(chr(39) + chr(34))}[/red]")
        raise typer.Exit(1) from exc
    except ops.SessionBusyError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("fork")
def fork(session_id: str) -> None:
    """Fork a session (copies every committed message) and print the new id."""

    async def go(svc: Services) -> str:
        return (await ops.fork_session(svc, session_id)).id

    print(_run_op(go))


@app.command("archive")
def archive(
    session_id: str,
    undo: Annotated[bool, typer.Option("--undo", help="Unarchive instead.")] = False,
) -> None:
    """Archive (hide from `list`) or, with --undo, unarchive a session."""

    async def go(svc: Services) -> None:
        await ops.set_archived(svc, session_id, not undo)

    _run_op(go)
    console.print(f"{'unarchived' if undo else 'archived'} {session_id}")


@app.command("delete")
def delete(session_id: str) -> None:
    """Delete a session and its messages (its events stay in the log)."""

    async def go(svc: Services) -> None:
        await ops.delete_session(svc, session_id)

    _run_op(go)
    console.print(f"deleted {session_id}")


@app.command("export")
def export(
    session_id: str,
    fmt: Annotated[str, typer.Option("--format", "-f", help="json | md")] = "md",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to a file.")] = None,
) -> None:
    """Export a session as JSON or a Markdown transcript."""

    async def go(svc: Services) -> str:
        return await ops.export_session(svc, session_id, fmt)

    try:
        text = _run_op(go)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if output is None:
        print(text, end="")
    else:
        output.write_text(text, encoding="utf-8")
        err_console.print(f"wrote {output}")
