"""`auoreo db upgrade|current|history` — Alembic against `<home>/state.db`."""

from __future__ import annotations

from typing import Annotated

import typer

from auoreo.cli.common import console, settings
from auoreo.core.db import alembic_config, migrate

app = typer.Typer(no_args_is_help=True)


def _url() -> str:
    return f"sqlite:///{settings().db_path}"


@app.command("upgrade")
def upgrade(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Migrate state.db to REVISION (default: head). Startup does this automatically."""
    path = settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    migrate(path, revision)
    console.print(f"[green]state.db at {revision}[/green] ({path})")


@app.command("current")
def current() -> None:
    """Show the revision state.db is at."""
    from alembic import command

    command.current(alembic_config(_url()), verbose=False)


@app.command("history")
def history() -> None:
    """List the migration chain."""
    from alembic import command

    command.history(alembic_config(_url()), verbose=False)
