"""`auoreo webhook list|test` — inbound webhook routes (`webhooks/<route>.yaml`)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.table import Table

from auoreo.automation import lock
from auoreo.automation.client import URL_HELP, check, local, remote
from auoreo.automation.deliveries import DeliveryWorker, Outbox
from auoreo.automation.views import webhook_infos
from auoreo.automation.webhooks import DELIVERY_HEADER, SIGNATURE_HEADER, secret_name, sign
from auoreo.cli.common import console, dump_json
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)
UrlOpt = Annotated[str | None, typer.Option("--url", envvar="AUOREO_URL", help=URL_HELP)]


@app.command("list")
def list_(url: UrlOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List routes (secrets are never shown, only whether they resolve)."""

    async def go(svc: Services) -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in webhook_infos(svc)]

    rows = check(remote("GET", url, "/api/webhooks")) if url else local(go)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("route", "provider", "events", "agent", "deliver", "enabled", "secret"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["path"],
            r["provider"],
            ",".join(r["events"]) or "*",
            r["agent"],
            ",".join(r["deliver"]),
            "yes" if r["enabled"] else "no",
            "set" if r["secret_configured"] else "MISSING",
        )
    console.print(table)


def _headers(provider: str, secret: str, body: bytes, event: str, delivery_id: str) -> dict[str, str]:
    event_header = "X-GitHub-Event" if provider == "github" else "X-Event"
    return {
        "Content-Type": "application/json",
        SIGNATURE_HEADER[provider]: sign(secret, body),
        DELIVERY_HEADER[provider]: delivery_id,
        event_header: event,
    }


@app.command("test")
def test(
    route: str,
    file: Annotated[Path, typer.Option("--file", "-f", exists=True, dir_okay=False, help="JSON payload")],
    event: Annotated[str, typer.Option(help="Event name header (X-GitHub-Event / X-Event)")] = "pull_request",
    delivery_id: Annotated[str | None, typer.Option("--delivery-id")] = None,
    url: UrlOpt = None,
    wait: Annotated[float, typer.Option(help="In-process: seconds to wait for the delivery")] = 120.0,
) -> None:
    """Sign a payload with the route's stored secret and POST it to /hooks/<route>."""
    body = file.read_bytes()
    json.loads(body)  # fail early on a broken file
    did = delivery_id or str(uuid.uuid4())

    async def go(svc: Services) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
        loaded = svc.harness.webhooks.get(route)
        if loaded is None:
            typer.echo(f"unknown route '{route}' (known: {sorted(svc.harness.webhooks)})", err=True)
            raise typer.Exit(1)
        m = loaded.manifest
        secret = svc.secrets.get(secret_name(m))
        if not secret:
            typer.echo(f"secret {m.secret} is not set (auoreo secrets set {secret_name(m)})", err=True)
            raise typer.Exit(1)
        headers = _headers(m.provider, secret, body, event, did)
        if url:
            r = remote("POST", url, f"/hooks/{route}", content=body, headers=headers, auth=False)
            return r.status_code, r.json(), None
        return await _in_process(svc, route, body, headers, wait)

    code, answer, delivered = local(go)
    typer.echo(f"{code} {json.dumps(answer)}")
    if delivered is not None:
        typer.echo(
            f"delivery {delivered['id']} {delivered['status']} {delivered.get('error') or ''}".rstrip()
        )
    if code >= 400:
        raise typer.Exit(1)


async def _in_process(
    svc: Services, route: str, body: bytes, headers: dict[str, str], wait: float
) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    from auoreo.api.app import create_app

    app_ = create_app(svc.settings, services=svc)
    app_.state.services = svc  # no lifespan: the Services already exist
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_), base_url="http://cli") as c:
        r = await c.post(f"/hooks/{route}", content=body, headers=headers)
    answer = r.json()
    if r.status_code != 202:
        return r.status_code, answer, None
    row_id = int(answer["delivery_id"])
    outbox = Outbox(svc)
    if not lock.acquire(svc.settings.home, svc):
        typer.echo("a running server holds the automation lock; it will deliver this row", err=True)
        return r.status_code, answer, None
    try:
        claimed = await outbox.claim(row_id)
        row = (
            await DeliveryWorker(svc).process(claimed)
            if claimed
            else await outbox.wait_terminal(row_id, wait)
        )
    finally:
        lock.release(svc.settings.home, svc)
    return r.status_code, answer, ({"id": row.id, "status": row.status, "error": row.error} if row else None)
