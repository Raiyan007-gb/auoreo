from __future__ import annotations

import socket
import sys
import webbrowser
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings


def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (0 = pick a free one).")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the URL in a browser.")] = False,
) -> None:
    """Run the API server (one app, one port)."""
    import uvicorn

    from auoreo.api.app import create_app

    st = settings(host=host, port=port, gateway=gateway)
    if not st.is_loopback and not st.server_password:
        err_console.print("[red]refusing to bind a non-loopback address without AUOREO_SERVER_PASSWORD[/red]")
        raise typer.Exit(2)
    sock = socket.socket(socket.AF_INET6 if ":" in st.host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((st.host, st.port))
    except OSError as exc:
        err_console.print(f"[red]cannot bind {st.host}:{st.port}: {exc}[/red]")
        raise typer.Exit(2) from exc
    bound = sock.getsockname()[1]
    url = f"http://{st.host}:{bound}"

    class _Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:  # announce only once the app (lifespan) is ready
                print(f"server listening on {url}", flush=True)  # discovery contract
                if open_browser:
                    webbrowser.open(url)

    config = uvicorn.Config(create_app(st), log_level="info", lifespan="on")
    server = _Server(config)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        sys.exit(0)
