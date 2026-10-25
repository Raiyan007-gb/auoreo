"""`auoreo env list` — execution environments and bridge peers with their status."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, settings, with_services
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List environments (over the daemon socket when one is running)."""
    from auoreo.remote import uds
    from auoreo.remote.environments import list_environments

    async def go(svc: Services) -> list[dict[str, Any]]:
        return [e.model_dump() for e in await list_environments(svc)]

    sock = uds.daemon_socket(settings().home)
    if sock is not None:
        with uds.client(sock) as c:
            rows = c.get("/api/environments").raise_for_status().json()
    else:
        rows = with_services(go, memory=False)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("name", "kind", "backend", "selected", "status", "detail"):
        table.add_column(col)
    for r in rows:
        status = {True: "[green]ok[/green]", False: "[red]down[/red]", None: "-"}[r["ok"]]
        detail = r["detail"] + (f" · {r['url']}" if r.get("url") else "")
        table.add_row(
            r["name"], r["kind"], r["backend"] or "-", "yes" if r["selected"] else "", status, detail
        )
    console.print(table)
