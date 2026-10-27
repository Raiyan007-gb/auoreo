"""`auoreo config` — the settings store in `<home>/state.db` (`app_settings`).

`get` prints effective values (real environment > flags > database > defaults), `list` the
stored keys only, `set`/`unset` validate the whole store before writing, `undo` restores the
previous change set and `edit` round-trips the store through $EDITOR as JSON. A running server
picks every change up live (its watcher polls the store's version counter).
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from auoreo.cli.common import dump_json, err_console, settings
from auoreo.core import env_store
from auoreo.core.paths import home_dir

app = typer.Typer(no_args_is_help=True)


def _write(data: dict[str, object]) -> None:
    from pydantic import ValidationError

    try:
        env_store.write(home_dir(), data, source="cli")
    except ValidationError as exc:
        lines = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10]]
        err_console.print("[red]refused (nothing changed):[/red]\n  " + "\n  ".join(lines))
        raise typer.Exit(1) from exc
    except env_store.EnvStoreError as exc:
        err_console.print(f"[red]refused: {exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("path")
def path() -> None:
    """Print where the settings live (state.db, table app_settings)."""
    print(env_store.store_path(home_dir()))


@app.command("get")
def get(key: Annotated[str | None, typer.Argument()] = None) -> None:
    """Print the effective settings (or one dotted key)."""
    data = settings().model_dump(mode="json", exclude={"server_password"})
    if key:
        for part in key.split("."):
            if not isinstance(data, dict) or part not in data:
                raise typer.BadParameter(f"unknown key '{key}'")
            data = data[part]
    dump_json(data)


@app.command("list")
def list_() -> None:
    """Print only the values stored in the database (no defaults, no environment)."""
    dump_json(env_store.read(home_dir()))


@app.command("set")
def set_(key: str, value: str) -> None:
    """Set a dotted key in the store. VALUE is parsed as JSON when possible."""
    if key.split(".")[0] in {"server_password"}:
        raise typer.BadParameter("secrets do not belong in the settings store; use `auoreo secrets set`")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    _write(env_store.set_path(env_store.read(home_dir()), key, parsed))
    print(f"{key} saved in {env_store.store_path(home_dir())}")


@app.command("unset")
def unset(key: str) -> None:
    """Remove a dotted key from the store (back to its default)."""
    _write(env_store.set_path(env_store.read(home_dir()), key, None))
    print(f"{key} removed")


@app.command("undo")
def undo() -> None:
    """Restore the previous change set (10 are kept)."""
    try:
        env_store.undo(home_dir())
    except env_store.EnvStoreError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    print("restored the previous settings")


# --- H4: `config edit` (spec §18): $EDITOR on a JSON dump, validate, then write back
@app.command("edit")
def edit() -> None:
    """Edit the stored settings as JSON in $EDITOR; an invalid result is refused (nothing changes)."""
    from auoreo.cli.config_edit import edit_settings

    edit_settings(home_dir())
