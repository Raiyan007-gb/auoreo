"""Filesystem locations: the runtime home (`~/.auoreo`) and the bundled definitions root."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Folder kinds the harness discovers. Order is display order only.
KINDS: tuple[str, ...] = (
    "agents",
    "tools",
    "skills",
    "workflows",
    "prompts",
    "permissions",
    "mcp",
    "commands",
    "backends",
    "hooks",
    "jobs",
    "webhooks",
    "plugins",
)

# --- W3-A: top-level folders shipped as bundled definitions (wheel `auoreo/_bundled/`,
# PyInstaller `sys._MEIPASS/`). `web/dist` travels with them as `<root>/web/dist`.
BUNDLED_DIRS: tuple[str, ...] = (*KINDS, "policies", "memory")


def home_dir() -> Path:
    """Runtime home. `AUOREO_HOME` wins; Windows uses %LOCALAPPDATA%\\auoreo."""
    env = os.environ.get("AUOREO_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "auoreo"
    return Path.home() / ".auoreo"


def bundled_root() -> Path:
    """Root holding the bundled `agents/ tools/ ...` folders.

    `AUOREO_BUNDLED_ROOT` wins. Otherwise use the checkout root (two levels above the
    `src/auoreo` package), which is where the bundled definitions live in this repo.
    """
    env = os.environ.get("AUOREO_BUNDLED_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # --- W3-A: a PyInstaller onefile unpacks the folders to sys._MEIPASS; a wheel install
    # ships them as the `auoreo/_bundled/` package data (see packaging/hatch_build.py).
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    packaged = Path(__file__).resolve().parents[1] / "_bundled"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3]


def web_dist_dir() -> Path:
    """The built SPA (`web/dist`) next to the bundled definitions (checkout, wheel or onefile)."""
    return bundled_root() / "web" / "dist"


def project_dir(cwd: Path | None = None) -> Path:
    """Project overlay root: `<cwd>/.auoreo`."""
    return (cwd or Path.cwd()) / ".auoreo"


def ensure_home(home: Path) -> Path:
    """Create the runtime home with owner-only permissions (POSIX)."""
    home.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        home.chmod(0o700)
    for sub in ("sessions", "cache"):
        (home / sub).mkdir(exist_ok=True)
    return home
