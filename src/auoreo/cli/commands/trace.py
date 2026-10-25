"""`auoreo trace summarize <file>`: per-span durations from an `AUOREO_TRACE_FILE`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from auoreo.core import tracefile

app = typer.Typer(no_args_is_help=True)


@app.command("summarize")
def summarize(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Trace-Event JSON file")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Count, total, p50 and p95 duration per span name (milliseconds)."""
    try:
        rows = tracefile.summarize(file)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"not a trace file: {exc}") from None
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("no complete (X) events")
        return
    typer.echo(f"{'span':<14}{'count':>7}{'total ms':>12}{'p50 ms':>10}{'p95 ms':>10}")
    for r in rows:
        typer.echo(
            f"{r['name']:<14}{r['count']:>7}{r['total_ms']:>12.1f}{r['p50_ms']:>10.1f}{r['p95_ms']:>10.1f}"
        )
