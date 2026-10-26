"""`auoreo web` — serve the API + UI and open it in a browser. On a non-loopback bind
(`--host 0.0.0.0`, password required) it also prints the LAN address(es) (I5)."""

from __future__ import annotations

from typing import Annotated

import typer

from auoreo.cli.commands.serve import serve


def web(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (0 = pick a free one).")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
) -> None:
    """Run the server and open the web UI."""
    serve(host=host, port=port, gateway=gateway, open_browser=True)
