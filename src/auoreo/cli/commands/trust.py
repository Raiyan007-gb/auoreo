"""`auoreo trust [path]`: trust a workspace's `.auoreo/` (hooks, MCP, tools, policies...)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import console, dump_json, settings
from auoreo.harness import trust as trust_mod


def trust(
    path: Annotated[
        Path | None, typer.Argument(help="Workspace folder (default: current directory).")
    ] = None,
    revoke: Annotated[
        bool, typer.Option("--revoke", help="Remove the workspace from the trust list.")
    ] = False,
    list_: Annotated[bool, typer.Option("--list", help="List trusted workspaces.")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Trust a workspace so its `.auoreo/` hooks, MCP servers, tools, plugins, policies and
    permission allow-rules load. Untrusted workspaces load only agents/skills/prompts/commands."""
    home = settings().home
    if list_:
        rows = trust_mod.listed(home)
        if as_json:
            dump_json(rows)
            return
        for key, meta in sorted(rows.items()):
            console.print(f"✓ {key}  ({meta.get('actor', 'user')})")
        return
    ws = (path or Path.cwd()).expanduser()
    if not ws.is_dir():
        raise typer.BadParameter(f"not a directory: {ws}")
    if revoke:
        gone = trust_mod.untrust(home, ws)
        console.print(f"{'revoked' if gone else 'was not trusted'}: {ws.resolve()}")
        return
    pending = trust_mod.skipped_in(ws)
    key = trust_mod.trust(home, ws)
    if as_json:
        dump_json({"workspace": key, "trusted": True, "unlocked": [f"{k}/{n}" for k, n in pending]})
        return
    console.print(f"[green]✓[/green] trusted {key}")
    for kind, name in pending:
        console.print(f"  now loads {kind}/{name}")
