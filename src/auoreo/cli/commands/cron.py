"""`auoreo cron list|run|pause|resume|runs` — scheduled jobs (`jobs/<name>.yaml`)."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.automation.client import URL_HELP, check, local, remote
from auoreo.automation.scheduler import scheduler_of
from auoreo.automation.views import job_infos, run_info
from auoreo.cli.common import console, dump_json
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)
UrlOpt = Annotated[str | None, typer.Option("--url", envvar="AUOREO_URL", help=URL_HELP)]
JsonOpt = Annotated[bool, typer.Option("--json")]


def _known(svc: Services, name: str) -> None:
    if name not in svc.harness.jobs:
        typer.echo(f"unknown job '{name}' (known: {sorted(svc.harness.jobs)})", err=True)
        raise typer.Exit(1)


@app.command("list")
def list_(url: UrlOpt = None, as_json: JsonOpt = False) -> None:
    """List jobs with their schedule, next fire time and last run."""

    async def go(svc: Services) -> list[dict[str, Any]]:
        return [j.model_dump(mode="json") for j in await job_infos(scheduler_of(svc))]

    rows = check(remote("GET", url, "/api/cron")) if url else local(go)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("name", "schedule", "enabled", "paused", "next run", "last run"):
        table.add_column(col)
    for r in rows:
        last = r["last_run"]
        table.add_row(
            r["name"],
            r["schedule"] + (f" ({r['tz']})" if r["tz"] else ""),
            "yes" if r["enabled"] else "no",
            "yes" if r["paused"] else "",
            str(r["next_run_at"] or r["error"] or "-"),
            f"{last['status']} #{last['id']} {last['scheduled_for']}" if last else "-",
        )
    console.print(table)


@app.command("run")
def run(name: str, url: UrlOpt = None) -> None:
    """Fire a job now. In-process this waits for the agent and the delivery."""
    if url:
        body = check(remote("POST", url, f"/api/cron/{name}/run"))
        typer.echo(f"run {body['run_id']} queued for {name}")
        return

    async def go(svc: Services) -> dict[str, Any]:
        _known(svc, name)
        row = await scheduler_of(svc).fire(name)
        assert row is not None
        return run_info(row).model_dump(mode="json")

    info = local(go)
    typer.echo(
        f"run {info['id']} {info['status']} session={info['session_id']} {info['error'] or ''}".rstrip()
    )
    if info["status"] != "ok":
        raise typer.Exit(1)


def _set_paused(name: str, paused: bool, url: str | None) -> None:
    if url:
        check(remote("POST", url, f"/api/cron/{name}/{'pause' if paused else 'resume'}"))
    else:

        async def go(svc: Services) -> None:
            _known(svc, name)
            await scheduler_of(svc).set_paused(name, paused)

        local(go)
    typer.echo(f"{'paused' if paused else 'resumed'} {name}")


@app.command("pause")
def pause(name: str, url: UrlOpt = None) -> None:
    """Pause a job (persists in <home>/cron_state.json)."""
    _set_paused(name, True, url)


@app.command("resume")
def resume(name: str, url: UrlOpt = None) -> None:
    """Resume a paused job."""
    _set_paused(name, False, url)


@app.command("runs")
def runs(name: str, limit: int = 20, url: UrlOpt = None, as_json: JsonOpt = False) -> None:
    """Recent runs of a job."""

    async def go(svc: Services) -> list[dict[str, Any]]:
        _known(svc, name)
        return [run_info(r).model_dump(mode="json") for r in await scheduler_of(svc).runs(name, limit)]

    rows = check(remote("GET", url, f"/api/cron/{name}/runs", params={"limit": limit})) if url else local(go)
    if as_json:
        dump_json(rows)
        return
    table = Table()
    for col in ("id", "scheduled_for", "status", "catch_up", "session", "error"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]),
            r["scheduled_for"],
            r["status"],
            "yes" if r["catch_up"] else "",
            r["session_id"] or "-",
            (r["error"] or "")[:60],
        )
    console.print(table)
