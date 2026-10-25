from __future__ import annotations

import os
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings
from auoreo.core.settings import Settings


def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (0 = pick a free one).")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the URL in a browser.")] = False,
    workers: Annotated[
        int | None,
        typer.Option(help="Worker processes (opt-in; >1 needs redis_url and sticky sessions)."),
    ] = None,
    uds: Annotated[
        Path | None,
        typer.Option(help="Also/instead serve on this Unix socket (0600; no token needed on it)."),
    ] = None,
) -> None:
    """Run the API server (one app, one port; optionally a Unix socket too)."""
    import uvicorn

    from auoreo.api.app import create_app

    st = settings(host=host, port=port, gateway=gateway, workers=workers)
    if not st.is_loopback and not st.server_password:
        err_console.print("[red]refusing to bind a non-loopback address without AUOREO_SERVER_PASSWORD[/red]")
        raise typer.Exit(2)
    _check_workers(st)
    if uds is not None:  # --- W2-B: the daemon's socket (TCP only when --port is given)
        _serve_uds(st, uds, tcp=port is not None)
        return
    sock = socket.socket(socket.AF_INET6 if ":" in st.host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((st.host, st.port))
    except OSError as exc:
        err_console.print(f"[red]cannot bind {st.host}:{st.port}: {exc}[/red]")
        raise typer.Exit(2) from exc
    bound = sock.getsockname()[1]
    url = f"http://{st.host}:{bound}"
    if st.workers > 1:
        _run_workers(st, sock, bound, url, open_browser)
        return

    class _Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:  # announce only once the app (lifespan) is ready
                print(f"server listening on {url}", flush=True)  # discovery contract
                if open_browser:
                    webbrowser.open(url)

        async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
            svc = getattr(app.state, "services", None)
            if svc is not None:  # close WS clients with 1001 before uvicorn's own 1012
                await svc.connections.close_all(1001)
            await super().shutdown(sockets=sockets)

    app = create_app(st)
    config = uvicorn.Config(app, log_level="info", lifespan="on")
    server = _Server(config)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        sys.exit(0)


def _check_workers(st: Settings) -> None:
    """Spec L1045: single process is normative; N > 1 needs Redis fan-out, else refuse."""
    if st.workers <= 1:
        return
    from auoreo.core.bus_redis import REFUSE_MSG

    if not st.redis_url:
        err_console.print(f"[red]refusing --workers {st.workers}: {REFUSE_MSG}[/red]")
        raise typer.Exit(2)
    try:
        import redis.asyncio  # noqa: F401
    except ImportError as exc:
        err_console.print("[red]--workers > 1 needs the `redis` extra (uv sync --extra redis)[/red]")
        raise typer.Exit(2) from exc


def _run_workers(st: Settings, sock: socket.socket, bound: int, url: str, open_browser: bool) -> None:
    """N uvicorn workers on our pre-bound socket; each builds its app through the factory
    (`create_app()` → `get_settings()`), so CLI overrides travel as AUOREO_* env vars."""
    import uvicorn
    from uvicorn.supervisors import Multiprocess

    os.environ.update(
        {
            "AUOREO_HOST": st.host,
            "AUOREO_PORT": str(bound),
            "AUOREO_GATEWAY": st.gateway,
            "AUOREO_WORKERS": str(st.workers),
        }
    )
    config = uvicorn.Config(
        "auoreo.api.app:create_app", factory=True, workers=st.workers, log_level="info", lifespan="on"
    )
    print(f"server listening on {url} ({st.workers} workers; sticky sessions required)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        Multiprocess(config, sockets=[sock]).run()
    except KeyboardInterrupt:
        sys.exit(0)


def _serve_uds(st: Settings, path: Path, *, tcp: bool) -> None:
    """Serve `create_app` on a Unix socket (+ TCP with --port). Requests over the socket are
    trusted by file permissions (`remote.uds.TrustUnixSocket`); TCP keeps bearer auth."""
    import uvicorn

    from auoreo.api.app import create_app
    from auoreo.remote import uds as uds_mod

    if not uds_mod.supported():
        err_console.print("[red]--uds needs a POSIX system with AF_UNIX[/red]")
        raise typer.Exit(2)
    if st.workers > 1:
        err_console.print("[red]--uds runs a single worker[/red]")
        raise typer.Exit(2)
    try:
        sockets = [uds_mod.bind_unix(path)]
        urls = [f"unix:{path}"]
        if tcp:
            tsock = socket.socket(socket.AF_INET6 if ":" in st.host else socket.AF_INET)
            tsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tsock.bind((st.host, st.port))
            sockets.append(tsock)
            urls.append(f"http://{st.host}:{tsock.getsockname()[1]}")
    except OSError as exc:
        err_console.print(f"[red]cannot bind: {exc}[/red]")
        raise typer.Exit(2) from exc
    inner = create_app(st)

    class _Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                print(f"server listening on {' and '.join(urls)}", flush=True)

        async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
            svc = getattr(inner.state, "services", None)
            if svc is not None:
                await svc.connections.close_all(1001)
            await super().shutdown(sockets=sockets)

    app = uds_mod.TrustUnixSocket(inner, st.server_password)
    server = _Server(uvicorn.Config(app, log_level="info", lifespan="on"))
    try:
        server.run(sockets=sockets)
    except KeyboardInterrupt:
        pass
    finally:
        path.unlink(missing_ok=True)
