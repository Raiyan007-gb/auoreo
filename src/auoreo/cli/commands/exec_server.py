"""`auoreo exec-server` — expose this machine's local environment to a remote AUOREO."""

from __future__ import annotations

import logging
import socket
import sys
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings
from auoreo.core.secrets import SecretStore


def _token(token_secret: str | None, token: str | None) -> str | None:
    if token_secret:
        if not token_secret.startswith("secret:"):
            err_console.print("[red]--token-secret must be a 'secret:<name>' reference[/red]")
            raise typer.Exit(2)
        st = settings()
        value = SecretStore(st.home, st.keyring).get(token_secret.removeprefix("secret:"))
        if not value:
            err_console.print(f"[red]secret '{token_secret}' is not set (auoreo secrets set …)[/red]")
            raise typer.Exit(2)
        return value
    return token


def exec_server(
    host: Annotated[
        str, typer.Option(help="Bind address (non-loopback needs --allow-remote).")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port (0 = pick a free one).")] = 7071,
    token_secret: Annotated[
        str | None, typer.Option(help="Bearer token as a 'secret:<name>' reference (preferred).")
    ] = None,
    token: Annotated[
        str | None,
        typer.Option(envvar="AUOREO_EXEC_TOKEN", help="Bearer token (env AUOREO_EXEC_TOKEN; avoid argv)."),
    ] = None,
    allow_remote: Annotated[bool, typer.Option(help="Permit a non-loopback bind (token required).")] = False,
    floor: Annotated[
        list[str] | None, typer.Option(help="Extra deny glob(s) on top of the hardline floors.")
    ] = None,
    workdir: Annotated[str | None, typer.Option(help="Default working directory for commands.")] = None,
) -> None:
    """Serve exec_command / write_stdin / close_session / ping over WebSocket JSON-RPC."""
    import uvicorn

    from auoreo.remote.exec_server import PATH, ExecServer, check_bind

    secret = _token(token_secret, token)
    try:
        check_bind(host, allow_remote=allow_remote, token=secret)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    assert secret is not None
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        err_console.print(f"[red]cannot bind {host}:{port}: {exc}[/red]")
        raise typer.Exit(2) from exc
    url = f"ws://{host}:{sock.getsockname()[1]}{PATH}"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server_app = ExecServer(secret, floors=floor or [], default_workdir=workdir).app()

    class _Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                print(f"exec-server listening on {url}", flush=True)

    server = _Server(uvicorn.Config(server_app, log_level="warning", lifespan="on"))
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        sys.exit(0)
