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


# M3 control plane: tunnels, web, logs
from auoreo.cli.commands import logs, tunnel, web  # noqa: E402

app.command("web")(web.web)
app.command("logs")(logs.logs)
app.add_typer(tunnel.app, name="tunnel", help="Expose the server via cloudflared / tailscale.")


from auoreo.cli.commands import cron, deliveries, webhook  # noqa: E402

app.add_typer(cron.app, name="cron", help="Scheduled jobs: list, run now, pause, resume, runs.")
app.add_typer(webhook.app, name="webhook", help="Inbound webhook routes: list, send a signed test payload.")
app.add_typer(deliveries.app, name="deliveries", help="Automation outbox: list, replay --dry-run.")
from auoreo.cli.commands import plugin  # noqa: E402

app.add_typer(plugin.app, name="plugin", help="List, add, remove, reload and pair plugins.")
