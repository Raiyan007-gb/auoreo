"""settings.json as the web edits it: validated, atomic, owner-only, with a 10-deep history.

`write(home, data)` validates the whole candidate by constructing `Settings` from it before the
file is touched (an invalid value leaves the file as it was), keeps the previous file in
`<home>/settings.history/` (newest 10) and replaces settings.json atomically with mode 0600.
`undo(home)` restores the newest history entry. `source_of()` says where an effective value
comes from: the real environment, a `.env` file, settings.json, secrets.db, a startup override
(`auoreo serve --port …`, tests) or the built-in default.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from auoreo.core.dotenv import from_dotenv
from auoreo.core.owner_files import write_owner_only

Source = Literal["env", "dotenv", "settings", "secrets", "override", "default"]
HISTORY_DIR = "settings.history"
HISTORY_KEEP = 10


class EnvStoreError(ValueError):
    pass


def settings_path(home: Path) -> Path:
    return home / "settings.json"


def read(home: Path) -> dict[str, Any]:
    path = settings_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvStoreError(f"settings.json is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvStoreError("settings.json must hold a JSON object")
    return data


def validate(home: Path, data: dict[str, Any]) -> None:
    """Raise `pydantic.ValidationError` unless `data` (the whole file) is a valid Settings."""
    from auoreo.core.settings import Settings

    Settings(home=home, **{k: v for k, v in data.items() if k != "home"})


def _history(home: Path) -> Path:
    return home / HISTORY_DIR


def _snapshot(home: Path) -> None:
    path = settings_path(home)
    body = path.read_bytes() if path.is_file() else b"{}\n"
    hist = _history(home)
    hist.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        hist.chmod(0o700)
    stamp = f"{time.time_ns()}"
    write_owner_only(hist / f"settings.{stamp}.json", body)
    for old in sorted(hist.glob("settings.*.json"))[:-HISTORY_KEEP]:
        old.unlink(missing_ok=True)


def write(home: Path, data: dict[str, Any], *, snapshot: bool = True) -> Path:
    """Validate, snapshot the current file, then replace settings.json atomically (0600)."""
    validate(home, data)
    if snapshot:
        _snapshot(home)
    path = settings_path(home)
    write_owner_only(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())
    return path


def history(home: Path) -> list[Path]:
    return sorted(_history(home).glob("settings.*.json"))


def undo(home: Path) -> Path:
    """Restore the newest history entry (it is consumed: undo twice goes two steps back)."""
    entries = history(home)
    if not entries:
        raise EnvStoreError("nothing to undo")
    newest = entries[-1]
    data = json.loads(newest.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise EnvStoreError("the history entry is not a JSON object")
    path = write(home, data, snapshot=False)
    newest.unlink(missing_ok=True)
    return path


def set_path(data: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    """A copy of `data` with `a.b.c` set (`value=None` removes the key: back to default)."""
    out: dict[str, Any] = json.loads(json.dumps(data))
    node = out
    *parents, leaf = dotted.split(".")
    for key in parents:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = node[key] = {}
        node = nxt
    if value is None:
        node.pop(leaf, None)
    else:
        node[leaf] = value
    return out


def env_source(name: str) -> Source | None:
    """`env` (real process env), `dotenv` (a .env file put it there), or None."""
    if name not in os.environ or os.environ[name] == "":
        return None
    return "dotenv" if from_dotenv(name) else "env"


def setting_source(name: str, field: str, file_data: dict[str, Any], overrides: dict[str, Any]) -> Source:
    if (src := env_source(name)) is not None:
        return src
    if field in overrides:
        return "override"
    return "settings" if field in file_data else "default"


__all__ = [
    "EnvStoreError",
    "Source",
    "env_source",
    "history",
    "read",
    "set_path",
    "setting_source",
    "settings_path",
    "undo",
    "validate",
    "write",
]
