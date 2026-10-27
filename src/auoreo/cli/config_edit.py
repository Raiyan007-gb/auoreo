"""H4: `auoreo config edit` — edit the stored settings (state.db) as JSON, validate, refuse invalid.

The store is dumped to a private 0600 temp file in the home; only a result that parses as a
JSON object, carries no secrets (`server_password`) or bootstrap fields, and validates against
the `Settings` model is written back (one transaction, previous content kept for undo). A
refused edit is kept for recovery and its path printed; the store is untouched."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import typer
from pydantic import ValidationError

from auoreo.cli.common import console, err_console

FORBIDDEN = ("server_password", "home", "db_encryption", "host", "port", "workers")


def pick_editor() -> list[str]:
    configured = None
    try:
        from auoreo.core.settings import Settings

        configured = Settings().cli2.editor
    except Exception:  # the file being edited may be the reason Settings fails
        configured = None
    raw = configured or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    raw = raw or ("notepad" if sys.platform == "win32" else "vi")
    return shlex.split(raw, posix=sys.platform != "win32")


def validate_text(text: str) -> tuple[dict[str, object] | None, str | None]:
    """(data, None) when acceptable, else (None, reason)."""
    from auoreo.core.settings import Settings

    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "the settings must be a JSON object"
    if bad := [k for k in FORBIDDEN if k in data]:
        return None, (
            f"not stored in the database ({', '.join(bad)}): secrets go to `auoreo secrets set`, "
            "bootstrap fields to the real environment or a CLI flag"
        )
    try:
        Settings.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]
        return None, "invalid settings:\n  " + "\n  ".join(lines)
    return data, None


def edit_settings(home: Path) -> None:
    from auoreo.core import env_store

    home.mkdir(parents=True, exist_ok=True)
    original = json.dumps(env_store.read(home), indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="settings.edit-", suffix=".json", dir=home)
    tmp = Path(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:  # mkstemp: 0600
        fh.write(original)
    argv = [*pick_editor(), str(tmp)]
    try:
        code = subprocess.call(argv)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        err_console.print(f"[red]cannot start editor {argv[0]!r}: {exc} (set $EDITOR or cli2.editor)[/red]")
        raise typer.Exit(1) from exc
    edited = tmp.read_text(encoding="utf-8")
    if code != 0:
        tmp.unlink(missing_ok=True)
        err_console.print(f"[red]editor exited with {code}; settings unchanged[/red]")
        raise typer.Exit(1)
    if edited == original:
        tmp.unlink(missing_ok=True)
        console.print("no changes")
        return
    data, reason = validate_text(edited)
    if reason is None and data is not None:
        try:
            env_store.write(home, dict(data), source="cli-edit")
        except (ValidationError, env_store.EnvStoreError) as exc:
            reason = str(exc)
    if reason is not None:
        err_console.print(f"[red]refused: {reason}[/red]\nsettings unchanged; your edit is kept at {tmp}")
        raise typer.Exit(1)
    tmp.unlink(missing_ok=True)
    console.print(f"saved to {env_store.store_path(home)}")
