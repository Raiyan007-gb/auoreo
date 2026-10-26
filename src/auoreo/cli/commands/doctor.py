"""`auoreo doctor`: report problems with a cause and remedy. Never repairs anything on its own;
`--fix` (I5) is a separate, confirmed step over `core/doctor_fix.py`'s safe fixes.

The checks live in `auoreo.core.diagnostics` (shared with `GET /api/doctor`); this module prints."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.markup import escape

from auoreo.cli.common import console, dump_json, settings
from auoreo.core import diagnostics
from auoreo.core.settings import Settings
from auoreo.harness.registry import Harness


def _backend_checks(st: Settings, h: Harness) -> list[dict[str, str]]:
    """Backend rows as dicts (kept for callers of the pre-diagnostics helper)."""
    return [c.as_dict() for c in asyncio.run(diagnostics.backend_checks(st, h))]


def _credential_checks(st: Settings) -> list[dict[str, str]]:
    return [c.as_dict() for c in diagnostics.credential_checks(st)]


async def _all_checks(st: Settings) -> list[diagnostics.Check]:
    from auoreo.core.diagnostics_cli import cli_checks  # --- G5a: live server, ws, tunnels, …

    return [*await diagnostics.run_checks(st), *await cli_checks(st)]


def _fix(st: Settings, yes: bool, as_json: bool) -> None:
    """--- I5: list the safe fixes, confirm each (or --yes), apply the accepted ones."""
    from auoreo.core import doctor_fix

    fixes = asyncio.run(doctor_fix.plan(st))
    done: list[dict[str, str]] = []
    if not fixes and not as_json:
        console.print("[green]✓[/green] nothing to fix")
    for f in fixes:
        line = f"{f.kind:8} {escape(f.target)} — {escape(f.detail)}"
        if as_json and not yes:
            continue  # JSON without --yes only lists the plan
        if not yes and not typer.confirm(f"fix: {f.kind} {f.target} ({f.detail})?", default=False):
            console.print(f"[dim]skipped[/dim] {line}")
            continue
        f.apply()
        done.append(f.as_dict())
        if not as_json:
            console.print(f"[green]fixed[/green] {line}")
    if as_json:
        dump_json({"planned": [f.as_dict() for f in fixes], "applied": done})


def doctor(
    as_json: Annotated[bool, typer.Option("--json")] = False,
    fix: Annotated[
        bool,
        typer.Option("--fix", help="Offer safe fixes (dirs, 0600 modes, stale worktrees), each confirmed."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="With --fix: apply every listed fix.")] = False,
) -> None:
    """Check the environment, definitions, database and credentials (`--fix`: safe repairs)."""
    st = settings()
    if fix:
        _fix(st, yes, as_json)
        return
    out = [c.as_dict() for c in asyncio.run(_all_checks(st))]
    if as_json:
        dump_json(out)
    else:
        icon = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "error": "[red]✗[/red]"}
        for c in out:
            # escape: details carry literal brackets ("[nc-opt-in]") that rich would eat as markup
            line = f"{icon[c['level']]} {escape(c['check']):12} {escape(c['detail'])}"
            if c["level"] != "ok" and c["remedy"]:
                line += f"\n    → {escape(c['remedy'])}"
            console.print(line)
    if any(c["level"] == "error" for c in out):
        raise typer.Exit(1)
