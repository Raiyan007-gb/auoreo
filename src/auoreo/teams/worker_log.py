"""Size-rotated JSON-lines log for kanban workers (spec §16.6 "2MiB log rotate").

`<home>/kanban/logs/<board>/<worker>.log`; when a write would push the file past
`max_bytes` it is renamed to `<worker>.log.1` (one generation kept) and a new file starts.
Lines are redacted and capped; I/O errors are logged, never raised.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from auoreo.core.redaction import redact

log = logging.getLogger(__name__)
LINE_CAP = 8_000


def worker_log_path(home: Path, board: str, worker: str) -> Path:
    return Path(home) / "kanban" / "logs" / board / f"{worker}.log"


class RotatingLog:
    def __init__(self, path: Path, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def _line(self, entry: dict[str, Any]) -> str:
        text = redact(json.dumps({"ts": time.time(), **entry}, default=str, ensure_ascii=False))
        if len(text) > LINE_CAP:
            text = json.dumps({"ts": time.time(), "type": entry.get("type"), "truncated": True})
        return text + "\n"

    def write(self, *entries: dict[str, Any]) -> None:
        if not entries:
            return
        blob = "".join(self._line(e) for e in entries).encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            size = self.path.stat().st_size if self.path.exists() else 0
            if size and size + len(blob) > self.max_bytes:
                self.path.replace(self.path.with_name(self.path.name + ".1"))
            with self.path.open("ab") as fh:
                fh.write(blob)
        except OSError as exc:
            log.warning("cannot write kanban worker log %s: %s", self.path, exc)
