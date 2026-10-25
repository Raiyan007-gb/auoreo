from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import console, err_console, settings
from auoreo.core.paths import bundled_root, home_dir
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness
from auoreo.memory.skill_lint import lint_file

app = typer.Typer(no_args_is_help=True)


@app.command("lint")
def lint(
    name: Annotated[str | None, typer.Argument(help="Skill name; omit to lint every skill.")] = None,
) -> None:
    """Lint discovered skills (name/folder, description 20-500 chars, body, no secrets). Exit 1 on errors."""
    harness = Harness(default_roots(home_dir(), Path.cwd(), bundled_root(), settings().extra_roots))
    skills = harness.skills
    if name is not None:
        if name not in skills:
            err_console.print(
                f"[red]✗[/red] unknown skill '{name}' (known: {', '.join(sorted(skills)) or 'none'})"
            )
            raise typer.Exit(1)
        skills = {name: skills[name]}
    failed = 0
    for skill in sorted(skills.values(), key=lambda s: s.name):
        errors = lint_file(skill.path)
        if errors:
            failed += 1
            err_console.print(f"[red]✗[/red] {skill.name} ({skill.source}: {skill.path})")
            for e in errors:
                err_console.print(f"    - {e}")
        else:
            console.print(f"[green]✓[/green] {skill.name} ({skill.source})")
    if failed:
        raise typer.Exit(1)
