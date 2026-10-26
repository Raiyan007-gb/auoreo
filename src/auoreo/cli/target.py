"""G5a: find the server a CLI command should talk to (spec §18 "call the API, not the DB").

Order: an explicit `--url` (http(s):// or `unix:<path>`) → the daemon socket → a server
answering `/api/health` on the configured host/port. Commands that can also work
in-process on `state.db` treat "no server" as their cue to do so."""

from __future__ import annotations

from typing import Any

import httpx
import typer

from auoreo.cli.common import err_console, settings
from auoreo.core.settings import Settings


def _headers(st: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {st.server_password}"} if st.server_password else {}


def client_for(url: str, st: Settings | None = None, timeout: float | None = None) -> httpx.Client:
    st = st or settings()
    t = httpx.Timeout(timeout if timeout is not None else st.cli.attach_timeout_s)
    if url.startswith("unix:"):
        sock = url.removeprefix("unix:").removeprefix("//")
        transport = httpx.HTTPTransport(uds=sock)
        return httpx.Client(transport=transport, base_url="http://auoreo", headers=_headers(st), timeout=t)
    return httpx.Client(base_url=url.rstrip("/"), headers=_headers(st), timeout=t)


def local_url(st: Settings) -> str:
    host = "127.0.0.1" if st.host in {"0.0.0.0", "::", ""} else st.host
    return f"http://{f'[{host}]' if ':' in host else host}:{st.port}"


def discover(url: str | None = None, st: Settings | None = None) -> str | None:
    """The server URL to use, or None when nothing is running (and none was named)."""
    st = st or settings()
    if url:
        return url
    from auoreo.remote import uds

    if (sock := uds.daemon_socket(st.home)) is not None:
        return f"unix:{sock}"
    candidate = local_url(st)
    try:
        r = httpx.get(candidate + "/api/health", timeout=1.0)
    except httpx.HTTPError:
        return None
    return candidate if r.status_code == 200 and r.json().get("status") == "ok" else None


def require(url: str | None = None, st: Settings | None = None) -> str:
    found = discover(url, st)
    if found is None:
        err_console.print("[red]no running server found; start `auoreo serve` or pass --url[/red]")
        raise typer.Exit(1)
    return found


def call(url: str, method: str, path: str, **kw: Any) -> Any:
    """One JSON request; HTTP errors become a clean exit 1 with the server's detail."""
    try:
        with client_for(url) as c:
            r = c.request(method, path, **kw)
    except httpx.HTTPError as exc:
        err_console.print(f"[red]{url}: {exc}[/red]")
        raise typer.Exit(1) from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        err_console.print(f"[red]{r.status_code}: {detail}[/red]")
        raise typer.Exit(1)
    return r.json() if r.content else None
