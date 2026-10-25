"""Typer application: wires sub-command groups from `cli/commands/`."""

from __future__ import annotations

import typer

from auoreo import __version__
from auoreo.cli.commands import config, db, doctor, harness, memory, run, secrets, serve, session, stats

app = typer.Typer(
    help="AUOREO — filesystem-native agent harness.",
    no_args_is_help=True,
    add_completion=True,
    pretty_exceptions_show_locals=False,
)
app.command("serve")(serve.serve)
app.command("run")(run.run)
app.command("doctor")(doctor.doctor)
app.add_typer(harness.app, name="harness", help="Inspect and validate definitions.")
app.add_typer(config.app, name="config", help="Read and write settings.json.")
app.add_typer(session.app, name="session", help="List sessions.")
app.add_typer(secrets.app, name="secrets", help="Manage the encrypted secret store.")
app.add_typer(memory.app, name="memory", help="Search and add long-term memory.")
app.command("stats")(stats.stats)
app.add_typer(db.app, name="db", help="Migrate and inspect state.db (Alembic).")


@app.command("version")
def version() -> None:
    """Print the version."""
    typer.echo(__version__)
