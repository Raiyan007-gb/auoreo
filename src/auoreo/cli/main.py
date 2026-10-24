"""`auoreo` CLI entry point. `--version` is answered before any heavy import."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in {"--version", "-V"}:
        from auoreo import __version__

        print(__version__)
        return
    from auoreo.cli.app import app

    app()


if __name__ == "__main__":
    main()
