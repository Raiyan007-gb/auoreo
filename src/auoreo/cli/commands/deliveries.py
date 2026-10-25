"""`auoreo deliveries list|replay` — the automation outbox."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.automation.client import URL_HELP, check, local, remote
from auoreo.automation.deliveries import Outbox
from auoreo.automation.views import delivery_info
from auoreo.cli.common import console, dump_json
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)
UrlOpt = Annotated[str | None, typer.Option("--url", envvar="AUOREO_URL", help=URL_HELP)]


@app.command("list")
def list_(
    status: Annotated[str | None, typer.Option(help="pending|delivering|delivered|failed|unknown")] = None,
    kind: Annotated[str | None, typer.Option(help="webhook|job|team")] = None,
    limit: int = 50,
    url: UrlOpt = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List deliveries, newest first."""
    params = {k: v for k, v in {"status": status, "kind": kind, "limit": limit}.items() if v is not None}

    async def go(svc: Services) -> list[dict[str, Any]]:
        return [delivery_info(r).model_dump(mode="json") for r in await Outbox(svc).list(status, kind, limit)]

    rows = check(remote("GET", url, "/api/deliveries", params=params)) if url else local(go)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("id", "kind", "source", "external_id", "status", "session", "error"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]),
            r["kind"],
            r["source"],
            r["external_id"][:24],
            r["status"],
            r["session_id"] or "-",
            (r["error"] or "")[:50],
        )
    console.print(table)


@app.command("replay")
def replay(
    delivery_id: int,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Only print the prompt.")] = True,
    url: UrlOpt = None,
) -> None:
    """Show the stored prompt (--dry-run, default) or queue a copy of the delivery."""
    if url:
        params = {"dry_run": str(dry_run).lower()}
        body = check(remote("POST", url, f"/api/deliveries/{delivery_id}/replay", params=params))
        typer.echo(body["prompt"])
        if body.get("delivery"):
            typer.echo(f"queued delivery {body['delivery']['id']}", err=True)
        return

    async def go(svc: Services) -> tuple[str, int | None]:
        outbox = Outbox(svc)
        row = await outbox.get(delivery_id)
        if row is None:
            typer.echo(f"delivery {delivery_id} not found", err=True)
            raise typer.Exit(1)
        prompt = str(row.payload.get("prompt") or "")
        if dry_run:
            return prompt, None
        if row.kind == "suggestion":
            typer.echo("suggestion deliveries are not replayable; propose it again", err=True)
            raise typer.Exit(1)
        new = await outbox.enqueue(
            row.kind, row.source, f"replay-{row.id}-{uuid.uuid4().hex[:8]}", dict(row.payload), row.target
        )
        return prompt, new.id if new else None

    prompt, new_id = local(go)
    typer.echo(prompt)
    if not dry_run:
        typer.echo(f"queued delivery {new_id}", err=True)
