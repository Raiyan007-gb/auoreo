"""Plaintext justifications for one-shot grants, kept for a short TTL (spec §25.10 L1021).

The audit row only ever carries `justificationHash` (sha256, 16 hex chars) and the `grantId`.
The plaintext lives in `<home>/egress/justifications.json` (0600) for `ttl_h` hours (default
24 h) so a reviewer can look up *why* a grant was made; every write and read purges expired
entries. Used for `net: direct` grants and sandbox-mode escalations.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def justification_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _path(home: Path) -> Path:
    return home / "egress" / "justifications.json"


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _live(data: dict[str, dict[str, Any]], now: float) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in data.items() if float(v.get("expires", 0)) > now}


def store(home: Path, grant_id: str, text: str, *, kind: str, tool: str, ttl_h: float = 24.0) -> str:
    """Keep `text` for `ttl_h` hours under `grant_id`; returns its hash (the audit value)."""
    digest = justification_hash(text)
    now = time.time()
    with _LOCK:
        path = _path(home)
        data = _live(_load(path), now)
        data[grant_id] = {
            "kind": kind,
            "tool": tool,
            "hash": digest,
            "text": text,
            "created": now,
            "expires": now + ttl_h * 3600,
        }
        _save(path, data)
    return digest


def lookup(home: Path, grant_id: str) -> dict[str, Any] | None:
    """The live entry for `grant_id` (expired entries are purged on the way)."""
    now = time.time()
    with _LOCK:
        path = _path(home)
        raw = _load(path)
        data = _live(raw, now)
        if len(data) != len(raw):
            _save(path, data)
    return data.get(grant_id)


def purge(home: Path, now: float | None = None) -> int:
    """Drop expired entries; returns how many were removed."""
    with _LOCK:
        path = _path(home)
        raw = _load(path)
        data = _live(raw, time.time() if now is None else now)
        if len(data) != len(raw):
            _save(path, data)
    return len(raw) - len(data)
