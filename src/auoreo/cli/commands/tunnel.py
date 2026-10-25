"""`auoreo tunnel start|stop|status` — expose the local server (password required)."""

from __future__ import annotations

from typing import Annotated

import typer

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.tunnels import TunnelError, TunnelInfo, TunnelManager, pid_alive

app = typer.Typer(no_args_is_help=True)


def _manager() -> tuple[TunnelManager, int]:
    st = settings()
    return TunnelManager(st.home, st.server_password), st.port


def _print(info: TunnelInfo, state: str) -> None:
    console.print(f"[green]{state}[/green] {info.provider} tunnel → port {info.port}")
    console.print(f"  url: {info.url}")
    console.print(f"  ws:  {info.ws_url}")


@app.command("start")
def start(
    provider: Annotated[str, typer.Option(help="cloudflared | tailscale | fake")] = "cloudflared",
    port: Annotated[int | None, typer.Option(help="Local server port (default: settings port).")] = None,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the public URL.")] = 30.0,
) -> None:
    """Start a tunnel to the running server and print its https:// and wss:// URLs."""
    mgr, default_port = _manager()
    try:
        info = mgr.start(provider, port or default_port, timeout)
    except TunnelError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    _print(info, "started")


@app.command("stop")
def stop() -> None:
    """Stop the tunnel recorded in `<home>/tunnel.json`."""
    mgr, _ = _manager()
    info = mgr.stop()
    if info is None:
        console.print("no tunnel running")
        return
    console.print(f"stopped {info.provider} tunnel ({info.url})")


@app.command("status")
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show the current tunnel, if any."""
    mgr, _ = _manager()
    info = mgr.status()
    if info is None:
        if as_json:
            dump_json(None)
        else:
            console.print("no tunnel running")
        raise typer.Exit(1)
    alive = info.pid is None or pid_alive(info.pid)
    if as_json:
        dump_json({**info.__dict__, "ws_url": info.ws_url, "alive": alive})
        return
    _print(info, "running" if alive else "stale (process gone)")
