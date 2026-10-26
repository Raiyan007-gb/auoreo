from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.paths import bundled_root, home_dir
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness, HarnessError

app = typer.Typer(no_args_is_help=True)


def _load(strict: bool = False) -> Harness:
    st = settings()
    return Harness(
        default_roots(home_dir(), Path.cwd(), bundled_root(), st.extra_roots),
        strict_overlays=strict,
        error_on_collision=st.tool_engine.error_on_collision,
    )


@app.command("list")
def list_(
    kind: Annotated[str | None, typer.Argument(help="tools | agents | skills | workflows | ...")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List discovered definitions and where each came from."""
    listing = _load().describe()
    if kind:
        listing = {kind: listing.get(kind, [])}
    if as_json:
        dump_json(listing)
        return
    for k, rows in listing.items():
        table = Table(title=k, show_lines=False)
        for col in ("name", "source", "description"):
            table.add_column(col, overflow="fold")
        for r in rows:
            table.add_row(r["name"], r["source"], (r["description"] or "")[:90])
        console.print(table)


@app.command("validate")
def validate(
    strict: Annotated[bool, typer.Option(help="Treat overlay errors as fatal.")] = True,
) -> None:
    """Validate every definition root. Exit 1 on any error."""
    try:
        h = _load(strict)
    except HarnessError as exc:
        for e in exc.errors:
            err_console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1) from exc
    for e in h.errors:
        err_console.print(f"[yellow]![/yellow] {e}")
    counts = {k: len(v) for k, v in h.describe().items()}
    console.print(f"[green]✓[/green] harness valid: {counts}")
    for t in h.tools.values():  # import every handler: catches syntax errors early
        try:
            t.handler()
        except Exception as exc:
            err_console.print(f"[red]✗[/red] tool {t.name}: {exc}")
            raise typer.Exit(1) from exc
    if h.errors:
        raise typer.Exit(1)


@app.command("roots")
def roots() -> None:
    """Show definition roots in precedence order."""
    st = settings()
    for r in default_roots(home_dir(), Path.cwd(), bundled_root(), st.extra_roots):
        mark = "✓" if r.path.is_dir() else "·"
        console.print(f"{mark} {r.source:10} {r.path}")
