"""Load `.env` files into the process environment for the CLI/server (never for tools).

Precedence: real environment > `<cwd>/.env` > `<home>/.env`. Blank values (`KEY=`) are
skipped so the placeholders in `.env.example` never shadow a key from the keyring or
`secrets.db`. Child processes still get a scrubbed environment (`core.secrets.child_env`),
and the executor floor still refuses tool reads of `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def load_env_files(cwd: Path | None = None) -> list[str]:
    """Merge `.env` files into `os.environ` without overriding; returns the names set."""
    loaded: list[str] = []
    candidates = [(cwd or Path.cwd()) / ".env"]
    for path in candidates:
        loaded += _merge(path)
    # AUOREO_HOME may itself come from the project .env, so resolve home afterwards.
    from auoreo.core.paths import home_dir

    home_env = home_dir() / ".env"
    if home_env not in candidates:
        loaded += _merge(home_env)
    return loaded


def _merge(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for name, value in dotenv_values(path).items():
        if not name or value is None or value.strip() == "" or name in os.environ:
            continue
        os.environ[name] = value
        out.append(name)
    return out
