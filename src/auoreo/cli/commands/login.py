"""H4: `auoreo login <connector>` — OAuth for a connector (logic in `core/oauth_login.py`).

Needs `connectors/<name>/oauth.yaml` in a definition root; without one it says so. The
refresh token goes to the secret store as `oauth/<connector>` (never printed)."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from auoreo.cli.common import console, err_console, settings
from auoreo.core import oauth_login as oauth
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.secrets import SecretStore, SecretStoreError
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
    io = oauth.Io(
        say=lambda msg: err_console.print(escape(msg)),
        ask=lambda label: typer.prompt(label, hide_input=True),
        open_url=_open,
        timeout_s=timeout,
    )
    try:
        cfg = oauth.load_config(path)
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
        SecretStore(st.home, st.keyring).set(oauth.secret_name(connector), str(refresh))
    except SecretStoreError as exc:
        err_console.print(f"[red]cannot store the refresh token: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"{connector}: signed in via {used}; refresh token stored as secret {oauth.secret_name(connector)}"
    )
