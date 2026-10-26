"""H4: `auoreo login <connector>` — OAuth for a connector (logic in `core/oauth_login.py`).

Needs `connectors/<name>/oauth.yaml` in a definition root; without one it says so. The
refresh token goes to the secret store as `oauth/<connector>` (never printed).

I7: after storing, the running server is told (`POST /api/oauth/<connector>/settled`) so it
drops a stale cached access token and emits `authorization/settled`; with no server running
the event is appended to the events table directly (the table is the source of truth)."""

from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from auoreo.cli.commands.plugin import _api
from auoreo.cli.common import console, err_console, settings
from auoreo.core import oauth_login as oauth
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.secrets import SecretStore, SecretStoreError
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots


def _open(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        return False


def login(
    connector: Annotated[str, typer.Argument(help="Connector name (connectors/<name>/oauth.yaml).")],
    flow: Annotated[str, typer.Option("--flow", help="auto | loopback | device | manual")] = "auto",
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds to wait for approval.")] = 300.0,
) -> None:
    """Sign in to a connector with OAuth (PKCE loopback → device code → manual code)."""
    st = settings()
    roots = [r.path for r in default_roots(ensure_home(st.home), Path.cwd(), bundled_root(), st.extra_roots)]
    path = oauth.find_config(connector, roots)
    if path is None:
        err_console.print(
            f"[red]no OAuth connector '{connector}': no connectors/{connector}/oauth.yaml in "
            f"{', '.join(str(r) for r in roots)}[/red]"
        )
        raise typer.Exit(1)
    store = SecretStore(st.home, st.keyring)
    try:
        cfg = oauth.load_config(path)
        io = oauth.Io(
            say=lambda msg: err_console.print(escape(msg)),
            ask=lambda label: typer.prompt(label, hide_input=True),
            open_url=_open,
            timeout_s=timeout,
            client_secret=oauth.client_secret_of(cfg, store),
        )
        used, tokens = oauth.login(cfg, io, flow)
    except oauth.OAuthError as exc:
        err_console.print(f"[red]login failed: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    refresh = tokens.get("refresh_token")
    if not refresh:
        err_console.print(
            f"[yellow]{connector}: signed in via {used}, but the provider issued no refresh token; "
            "nothing was stored (access tokens are never persisted)[/yellow]"
        )
        raise typer.Exit(1)
    try:
        store.set(oauth.secret_name(connector), str(refresh))
    except SecretStoreError as exc:
        err_console.print(f"[red]cannot store the refresh token: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"{connector}: signed in via {used}; refresh token stored as secret {oauth.secret_name(connector)}"
    )
    settle(st, connector, used)


def settle(st: Settings, connector: str, flow: str) -> str:
    """Emit `authorization/settled`: through the running server, else straight into the DB."""
    try:
        res = _api(st, None, "POST", f"/api/oauth/{connector}/settled", {"flow": flow})
    except typer.Exit:
        res = None
    if res is not None:
        return "server"
    try:
        asyncio.run(_append_offline(st, {"connector": connector, "flow": flow}))
    except Exception as exc:  # the login itself succeeded; the event is best effort
        err_console.print(f"[yellow]could not record {oauth.SETTLED_EVENT}: {escape(str(exc))}[/yellow]")
        return "none"
    return "offline"


async def _append_offline(st: Settings, data: dict[str, Any]) -> None:
    from auoreo.core.bus import EventBus
    from auoreo.core.db import Database
    from auoreo.core.event_store import EventStore

    db = Database(st.db_path)
    await db.init()
    try:
        await EventStore(db, EventBus()).append("server", oauth.SETTLED_EVENT, data)
    finally:
        await db.close()
