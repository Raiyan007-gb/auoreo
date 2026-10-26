"""I5: stdout guard for `auoreo run --format stream-json` (spec §17 "install stdout guard in
headless/stream mode (non-SSE stdout → stderr)").

While active, `sys.stdout` IS stderr: any stray `print()`, rich console output, warnings or a
logging handler that wrote to stdout lands on stderr instead. Only `write_line()` reaches the
real stdout, so a consumer can parse stdout as pure JSON lines.
"""

from __future__ import annotations

import logging
import sys
from types import TracebackType
from typing import TextIO

_real: list[TextIO] = []


def write_line(text: str) -> None:
    """One protocol line on the real stdout (guarded or not)."""
    out = _real[-1] if _real else sys.stdout
    out.write(text + "\n")
    out.flush()


def active() -> bool:
    return bool(_real)


class StdoutGuard:
    def __init__(self) -> None:
        self._moved: list[logging.StreamHandler[TextIO]] = []

    def __enter__(self) -> StdoutGuard:
        real = sys.stdout
        _real.append(real)
        sys.stdout = sys.stderr
        loggers = [logging.getLogger(), *(logging.getLogger(n) for n in logging.root.manager.loggerDict)]
        for lg in loggers:
            for h in getattr(lg, "handlers", []):
                if isinstance(h, logging.StreamHandler) and h.stream is real:
                    h.setStream(sys.stderr)
                    self._moved.append(h)
        return self

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        real = _real.pop()
        sys.stdout.flush()
        sys.stdout = real
        for h in self._moved:
            h.setStream(real)
