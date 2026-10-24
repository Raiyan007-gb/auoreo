from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, with_services
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_(limit: int = 20, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
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
            for r in await svc.list_sessions(limit)
        ]

    rows = with_services(go, memory=False)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("id", "title", "agent", "status", "updated"):
        table.add_column(col)
    for r in rows:
        table.add_row(*(r[c] for c in ("id", "title", "agent", "status", "updated")))
    console.print(table)
