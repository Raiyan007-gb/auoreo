"""H4: `auoreo --profile/-p <name>` (spec §18, §16.3, §25.14 multiprofile isolation).

A profile is a separate runtime home, `<base home>/profiles/<name>/`: its own settings.json,
state.db, memory.db, secrets.db, cron state, plugins and `mcp/` definitions. Nothing is
shared with the base home (spec: "never shared root"). Selecting a profile sets
`AUOREO_HOME` for this process before any command builds its settings, so every command,
service and child process sees the same home.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import typer

from auoreo.core.paths import ensure_home, home_dir

PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ENV = "AUOREO_PROFILE"


def profile_home(name: str, base: Path | None = None) -> Path:
    if not PROFILE_RE.match(name):
        raise typer.BadParameter(
            f"invalid profile name '{name}' (lowercase letters, digits, '-' and '_'; max 64)",
            param_hint="--profile",
        )
    return (base or home_dir()) / "profiles" / name


def apply_profile(name: str | None) -> Path | None:
    """Point `AUOREO_HOME` at the profile's home (created 0700). None → base home unchanged.

    Idempotent: a home that already is this profile's home is kept (no `profiles/x/profiles/x`)."""
    if not name:
        return None
    current = home_dir()
    if current.parent.name == "profiles" and current.name == name:
        return current
    home = ensure_home(profile_home(name, current))
    os.environ["AUOREO_HOME"] = str(home)
    os.environ[ENV] = name
    from auoreo.core.settings import get_settings

    get_settings.cache_clear()  # anything cached before the switch belongs to the base home
    return home
