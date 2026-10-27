"""I5 CLI surface (spec §18), registered onto existing groups by `cli/app.py`:

- `auoreo command|agent|model|config|lsp|mcp reload` → `POST /api/harness/reload?kind=…`
- `auoreo plugin get|set` (config values; secrets refused) and `plugin install` (alias of add)
- top-level `auoreo queue SESSION MESSAGE` (alias of `session queue`)

Thin wrappers: the logic lives in `core/reload_kinds.py` and `plugins/config_values.py`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from auoreo.cli import target
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.cli.output import want_json
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.harness.discovery import default_roots
from auoreo.harness.manifests import PluginManifest
from auoreo.harness.registry import Harness
from auoreo.plugins import config_values as cv

UrlOpt = Annotated[str | None, typer.Option("--url", help="Server URL (default: discover the local one).")]
JsonOpt = Annotated[bool, typer.Option("--json")]
RELOAD_LABELS = {
    "command": "slash commands",
    "agent": "agents",
    "model": "model routing settings",
    "config": "stored settings",
    "lsp": "language servers",
    "mcp": "MCP servers",
}


def _print_reload(kind: str, res: dict[str, Any]) -> None:
    console.print(f"reloaded {RELOAD_LABELS[kind]} (generation {res['generation']})")
    for group, parts in res.get("changes", {}).items():
        for what, names in parts.items():
            if names:
                console.print(f"  {group} {what}: {escape(', '.join(names))}")
    if res.get("note"):
        console.print(f"  note: {escape(res['note'])}")
    for e in res.get("errors", []):
        err_console.print(f"[yellow]! {escape(e)}[/yellow]")


def reload_command(kind: str) -> Callable[..., None]:
    def reload(url: UrlOpt = None, as_json: JsonOpt = False) -> None:
        base = target.discover(url)
        if base is None:
            label = RELOAD_LABELS[kind]
            err_console.print(f"[yellow]no running server: {label} load when `auoreo serve` starts[/yellow]")
            raise typer.Exit(1)
        res = target.call(base, "POST", "/api/harness/reload", params={"kind": kind})
        dump_json(res) if want_json(as_json) else _print_reload(kind, res)

    reload.__doc__ = f"Reload {RELOAD_LABELS[kind]} on the running server (takes effect at the next turn)."
    return reload


# ------------------------------------------------------------------ plugin get/set
def _manifest(name: str) -> PluginManifest:
    st = settings()
    h = Harness(default_roots(ensure_home(st.home), Path.cwd(), bundled_root(), st.extra_roots))
    d = h.plugins.get(name)
    if d is None:
        err_console.print(
            f"[red]unknown plugin '{name}' (known: {', '.join(sorted(h.plugins)) or 'none'})[/red]"
        )
        raise typer.Exit(1)
    manifest: PluginManifest = d.manifest
    return manifest


def plugin_get(
    name: str,
    key: Annotated[str | None, typer.Argument(help="One key (default: every value).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Show a plugin's config values from the settings store (secret-looking keys are masked)."""
    st = settings()
    m = _manifest(name)
    vals = cv.values(st.home, st.plugins.get(name, {}), m)
    if key is not None:
        if key not in vals:
            err_console.print(f"[red]{name} has no value for '{key}'[/red]")
            raise typer.Exit(1)
        vals = {key: vals[key]}
    if want_json(as_json):
        dump_json(vals)
        return
    for k, v in vals.items():
        console.print(f"{k} = {escape(json.dumps(v) if not isinstance(v, str) else v)}")
    if not vals:
        console.print(f"{name}: no config values set")


def plugin_set(
    name: str,
    key: str,
    value: Annotated[str, typer.Argument(help='JSON (true, 3, ["a"]) or a plain string.')],
) -> None:
    """Set one plugin config value in the settings store (validated; secrets are refused)."""
    st = settings()
    try:
        parsed = cv.set_value(ensure_home(st.home), _manifest(name), key, value)
    except cv.PluginConfigError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"plugins.{name}.{key} = {escape(json.dumps(parsed))}")
    console.print("restart or `auoreo plugin reload <name>` to apply it to a running plugin")


def register(app: typer.Typer) -> None:
    from auoreo.cli.commands import catalog, config, lsp, mcp, models, pairing, plugin, session_more

    for group, kind in (
        (catalog.command_app, "command"),
        (catalog.agent_app, "agent"),
        (models.app, "model"),
        (config.app, "config"),
        (lsp.app, "lsp"),
        (mcp.app, "mcp"),
    ):
        group.command("reload")(reload_command(kind))
    plugin.app.command("get")(plugin_get)
    plugin.app.command("set")(plugin_set)
    plugin.app.command("install", help="Alias of `plugin add` (lands quarantined).")(plugin.add)
    app.command("queue", help="Queue a follow-up turn on a live session (alias of `session queue`).")(
        session_more.queue
    )
    app.add_typer(
        pairing.app, name="pairing", help="Chat pairing for connector plugins: list, approve, revoke."
    )
