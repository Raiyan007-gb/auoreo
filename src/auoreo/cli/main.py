"""`auoreo` CLI entry point. `--version` is answered before any heavy import."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in {"--version", "-V"}:
        from auoreo import __version__

        print(__version__)
        return
    # Freeze yolo + the redaction switch from the REAL process env (§19.3). No `.env` file is
    # ever loaded: settings live in state.db, keys in secrets.db (Settings → Environment/Models).
    import auoreo.core.permissions
    import auoreo.core.redaction  # noqa: F401

    _utf8_console()
    _configure_logging()
    from auoreo.cli.app import app

    app()


def _utf8_console() -> None:
    """Windows consoles default to a legacy code page (cp1252) that cannot print ✓ or →;
    write UTF-8 instead, replacing anything the terminal still cannot show."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and (stream.encoding or "").lower().replace("-", "") != "utf8":
            reconfigure(encoding="utf-8", errors="replace")


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
