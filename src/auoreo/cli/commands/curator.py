from __future__ import annotations

from typing import Annotated

import typer

from auoreo.cli.common import console, dump_json, settings, with_services
from auoreo.core.services import Services
from auoreo.memory import curator as curator_mod
from auoreo.schema.chat import CuratorReport

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change; touch nothing.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Ignore the idle and interval guards.")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic memory curator (archive unused, merge duplicates; never deletes)."""

    async def go(svc: Services) -> CuratorReport:
        return await curator_mod.get(svc).run(dry_run, force=force)

    rep = with_services(go)
    if as_json:
        dump_json(rep.model_dump())
        return
    if rep.skipped:
        console.print(f"curator skipped: {rep.skipped} (use --force to run anyway)")
        raise typer.Exit(0)
    verb = "would archive" if dry_run else "archived"
    console.print(
        f"scanned {rep.scanned} · {verb} {len(rep.archived)} {rep.archived} · stale {len(rep.stale)} "
        f"{rep.stale} · merged {len(rep.duplicates_merged)} · pinned skipped {rep.skipped_pinned} · "
        f"contradictions {len(rep.contradictions)}"
    )
    console.print(f"report: {rep.report_path}\nledger: {rep.ledger_path}", soft_wrap=True)


@app.command("report")
def report() -> None:
    """Print the most recent curator REPORT.md (includes pending contradiction flags)."""
    path = curator_mod.latest_report(settings().home)
    if path is None:
        typer.echo("no curator report yet (run `auoreo curator run`)", err=True)
        raise typer.Exit(1)
    typer.echo(f"# {path}\n")
    typer.echo(path.read_text(encoding="utf-8"))
