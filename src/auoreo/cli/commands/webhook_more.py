"""G5a: `auoreo webhook add|deliveries|replay` (spec §18; `list|test|rotate` live in webhook.py).

`add` writes `webhooks/<route>.yaml` with a `secret:` *reference* only; the generated secret
goes into the secret store and is printed once (the sender needs it), never into the file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from pydantic import ValidationError
from rich.table import Table

from auoreo.automation.client import check, local, remote
from auoreo.automation.deliveries import Outbox
from auoreo.automation.views import delivery_info
from auoreo.automation.webhook_verify import new_secret
from auoreo.cli.commands.webhook import UrlOpt, app
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.services import Services
from auoreo.harness.manifests import WebhookManifest

DEFAULT_PROMPT = "Webhook {{route}} received event {{event.action}}. Summarise what happened."


@app.command("add")
def add(
    route: str,
    provider: Annotated[str, typer.Option(help="github | gitlab | svix | generic")] = "generic",
    event: Annotated[
        list[str] | None, typer.Option("--event", help="Accepted event name (repeatable).")
    ] = None,
    agent: Annotated[str, typer.Option(help="Agent that handles the payload.")] = "default",
    prompt: Annotated[str | None, typer.Option(help="Prompt template over {{event.*}}.")] = None,
    prompt_file: Annotated[Path | None, typer.Option(help="Read the prompt template from a file.")] = None,
    deliver: Annotated[
        list[str] | None, typer.Option("--deliver", help="Delivery target (repeatable).")
    ] = None,
    description: Annotated[str | None, typer.Option(help="One-line description.")] = None,
    project: Annotated[
        bool, typer.Option("--project/--global", help="<cwd>/.auoreo/webhooks (default) or ~/.auoreo.")
    ] = True,
    disabled: Annotated[bool, typer.Option("--disabled", help="Write it with enabled: false.")] = False,
    no_secret: Annotated[bool, typer.Option("--no-secret", help="Do not generate/store a secret.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing route file.")] = False,
) -> None:
    """Create a webhook route file (+ a stored secret, printed once)."""
    st = settings()
    text = prompt_file.read_text(encoding="utf-8") if prompt_file else (prompt or DEFAULT_PROMPT)
    data: dict[str, Any] = {
        "name": route,
        "description": description or f"{provider} webhook {route}",
        "provider": provider,
        "secret": f"secret:webhook/{route}",
        "events": list(event or []),
        "agent": agent,
        "prompt": text,
        "deliver": list(deliver or ["log"]),
        "enabled": not disabled,
    }
    try:
        WebhookManifest.model_validate(data)
    except ValidationError as exc:
        err_console.print(f"[red]invalid route: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}[/red]")
        raise typer.Exit(2) from exc
    root = (Path.cwd() / ".auoreo") if project else st.home
    path = root / "webhooks" / f"{route}.yaml"
    if path.exists() and not force:
        err_console.print(f"[red]{path} exists (use --force to overwrite)[/red]")
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    err_console.print(f"wrote {path}")
    if no_secret:
        err_console.print(f"set its secret with: auoreo secrets set webhook/{route}")
        return
    from auoreo.core.secrets import SecretStore

    fresh = new_secret(provider)
    SecretStore(st.home, st.keyring).set(f"webhook/{route}", fresh)
    typer.echo(fresh)
    err_console.print(
        f"stored as secret webhook/{route}; paste it into the sender. It is not shown again "
        f"(rotate with `auoreo webhook rotate {route}`)."
    )


@app.command("deliveries")
def deliveries(
    route: str,
    status: Annotated[str | None, typer.Option(help="pending|delivering|delivered|failed|unknown")] = None,
    limit: int = 50,
    url: UrlOpt = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Deliveries received on one route, newest first."""
    params: dict[str, Any] = {"kind": "webhook", "limit": 1000}
    if status:
        params["status"] = status

    async def go(svc: Services) -> list[dict[str, Any]]:
        rows = await Outbox(svc).list(status, "webhook", 1000)
        return [delivery_info(r).model_dump(mode="json") for r in rows]

    rows = check(remote("GET", url, "/api/deliveries", params=params)) if url else local(go)
    rows = [r for r in rows if r.get("source") == route][: max(limit, 1)]
    if as_json:
        dump_json(rows)
        return
    table = Table(title=f"webhook {route}")
    for col in ("id", "external_id", "status", "session", "created", "error"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]),
            str(r["external_id"])[:24],
            r["status"],
            r.get("session_id") or "-",
            str(r.get("created_at", ""))[:19],
            (r.get("error") or "")[:50],
        )
    console.print(table)


@app.command("replay")
def replay(
    delivery_id: int,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Only print the prompt.")] = True,
    url: UrlOpt = None,
) -> None:
    """Show (default) or re-queue a webhook delivery's stored prompt."""
    from auoreo.cli.commands import deliveries as deliveries_cmd

    async def kind_of(svc: Services) -> str | None:
        row = await Outbox(svc).get(delivery_id)
        return row.kind if row else None

    if not url:
        kind = local(kind_of)
        if kind is None:
            err_console.print(f"[red]delivery {delivery_id} not found[/red]")
            raise typer.Exit(1)
        if kind != "webhook":
            err_console.print(f"[red]delivery {delivery_id} is a {kind} delivery, not a webhook[/red]")
            raise typer.Exit(1)
    deliveries_cmd.replay(delivery_id, dry_run=dry_run, url=url)
