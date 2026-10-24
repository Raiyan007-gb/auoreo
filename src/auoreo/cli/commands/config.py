from __future__ import annotations

import json
from typing import Annotated

import typer

from auoreo.cli.common import dump_json, settings
from auoreo.core.paths import home_dir
from auoreo.core.settings import write_settings_value

app = typer.Typer(no_args_is_help=True)


@app.command("path")
def path() -> None:
    """Print the settings.json path."""
    print(home_dir() / "settings.json")


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


@app.command("set")
def set_(key: str, value: str) -> None:
    """Set a dotted key in settings.json. VALUE is parsed as JSON when possible."""
    if key in {"server_password"}:
        raise typer.BadParameter("secrets do not belong in settings.json; use `auoreo secrets set`")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    print(write_settings_value(home_dir(), key, parsed))
