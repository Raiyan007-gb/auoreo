"""`auoreo daemon start|stop|status` — the app detached on `<home>/auoreo.sock` (POSIX)."""

from __future__ import annotations

from typing import Annotated

import typer

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.remote import daemon, uds

app = typer.Typer(no_args_is_help=True)


def _paths() -> daemon.DaemonPaths:
    if not uds.supported():
        err_console.print("[red]auoreo daemon is unsupported on this platform (needs Unix sockets)[/red]")
        raise typer.Exit(2)
    return daemon.DaemonPaths(settings().home)


@app.command("start")
def start(
    port: Annotated[int | None, typer.Option(help="Also listen on TCP (bearer auth applies).")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
) -> None:
    """Start the daemon on <home>/auoreo.sock (0600; no token needed on the socket)."""
    paths = _paths()
    try:
        pid, health = daemon.start(paths, port=port, gateway=gateway)
    except daemon.DaemonError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"daemon started (pid {pid}) on unix:{paths.sock} · {health.get('version')}")


@app.command("stop")
def stop() -> None:
    """SIGTERM the daemon and wait up to 10 s."""
    paths = _paths()
    try:
        pid = daemon.stop(paths)
    except daemon.DaemonError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"daemon stopped (pid {pid})" if pid else "daemon is not running")


@app.command("status")
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Health check over the socket; exit 1 when the daemon is not answering."""
    info = daemon.status(_paths())
    if as_json:
        dump_json(info)
    elif info["running"]:
        h = info["health"]
        where = f"running (pid {info['pid']}) on unix:{info['socket']}"
        console.print(f"{where} · v{h.get('version')} · gateway {h.get('gateway')}")
    else:
        console.print(f"not running (socket {info['socket']})")
    if not info["running"]:
        raise typer.Exit(1)
