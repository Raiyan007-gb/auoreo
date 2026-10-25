"""Append-only JSONL ledger of memory lifecycle mutations (spec §24.3).

`<home>/memory/.curator_ledger.jsonl`, one object per line:
`{ts, actor: "curator"|"user"|"agent", action, fact_id, into?, reason}`. Actions:
`archive`, `merge`, `forget`, `purge`, `pin`, `unpin`, `flag_contradiction`. The ledger is
an audit trail for humans and the curator report; the facts table stays the source of truth.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
LEDGER = ".curator_ledger.jsonl"


def ledger_path(home: Path) -> Path:
    return home / "memory" / LEDGER


def append(home: Path, entries: list[dict[str, Any]]) -> Path:
    path = ledger_path(home)
    if not entries:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps({"ts": now, **entry}, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def entry(actor: str, action: str, fact_id: int, reason: str, into: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"actor": actor, "action": action, "fact_id": fact_id, "reason": reason}
    if into is not None:
        out["into"] = into
    return out


def read(home: Path) -> list[dict[str, Any]]:
    """Every parseable line (a torn last line from a crash is skipped, not fatal)."""
    path = ledger_path(home)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.warning("skipping unreadable ledger line in %s", path)
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
