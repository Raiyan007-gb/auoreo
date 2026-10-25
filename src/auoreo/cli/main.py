"""`auoreo` CLI entry point. `--version` is answered before any heavy import."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in {"--version", "-V"}:
        from auoreo import __version__

        print(__version__)
        return
    from auoreo.core.dotenv import load_env_files

    load_env_files()  # provider keys + AUOREO_* from .env (never overrides the real env)
    _configure_logging()
    from auoreo.cli.app import app

    app()


def _configure_logging() -> None:
    """Redacting formatter on every handler before any command runs (spec §19.10)."""
    from auoreo.core.logsetup import configure_logging

    try:
        from auoreo.core.settings import get_settings

        st = get_settings()
    except Exception:  # an invalid config is reported by the command itself
        st = None
    configure_logging(st)


if __name__ == "__main__":
    main()
