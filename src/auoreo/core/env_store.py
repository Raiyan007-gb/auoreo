"""The managed settings as the web and CLI edit them: validated, stored in state.db, undoable.

`write(home, data)` validates the whole candidate by constructing `Settings` from it before
anything is stored (an invalid value leaves the store as it was), snapshots the previous content
(newest 10, `settings_store.HISTORY_KEEP`) and replaces the `app_settings` rows in one
transaction. `undo(home)` restores the newest snapshot. `source_of()` says where an effective
value comes from: the real environment, the database (`db`), secrets.db, a startup override
(`auoreo serve --port …`, tests) or the built-in default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from auoreo.core import settings_store
from auoreo.core.settings_store import BOOTSTRAP, SettingsStoreError

Source = Literal["env", "db", "secrets", "override", "default"]
EnvStoreError = SettingsStoreError


def store_path(home: Path) -> Path:
    return settings_store.db_file(home)


def read(home: Path) -> dict[str, Any]:
    return settings_store.read(home)


def validate(home: Path, data: dict[str, Any]) -> None:
    """Raise `pydantic.ValidationError` unless `data` (the whole store) is a valid Settings."""
    from auoreo.core.settings import Settings

    Settings(home=home, **{k: v for k, v in data.items() if k != "home"})


def write(home: Path, data: dict[str, Any], *, snapshot: bool = True, source: str = "api") -> Path:
    """Validate, then replace the stored settings (previous content kept for undo)."""
    settings_store._clean(data)
    validate(home, data)
    settings_store.write(home, data, source=source, snapshot=snapshot)
    return store_path(home)


def history(home: Path) -> int:
    """Change sets available to undo."""
    return settings_store.history_count(home)


def undo(home: Path) -> Path:
    """Restore the newest snapshot (it is consumed: undo twice goes two steps back)."""
    data = settings_store.pop_history(home)
    data = {k: v for k, v in data.items() if k not in BOOTSTRAP}
    settings_store.write(home, data, source="undo", snapshot=False)
    return store_path(home)


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
    """`env` when the real process environment sets `name`, else None."""
    return "env" if os.environ.get(name, "") != "" else None


def setting_source(name: str, field: str, stored: dict[str, Any], overrides: dict[str, Any]) -> Source:
    if (src := env_source(name)) is not None:
        return src
    if field in overrides:
        return "override"
    return "db" if field in stored else "default"


__all__ = [
    "EnvStoreError",
    "Source",
    "env_source",
    "history",
    "read",
    "set_path",
    "setting_source",
    "store_path",
    "undo",
    "validate",
    "write",
]
