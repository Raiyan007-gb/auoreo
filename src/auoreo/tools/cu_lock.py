"""`computer-use.lock` (spec §14.2/§24.6): one desktop driver at a time across processes.

`O_CREAT | O_EXCL` creates `<home>/computer-use.lock` holding `{pid, session_id, ts}`.
Outcomes: `fresh` (we created it), `reentrant` (this process + session already holds it;
a per-process refcount keeps it until the last release), `blocked` (a live other holder —
`LockBlocked`). A lock whose pid is dead, or that is unreadable and older than
`CORRUPT_GRACE_S`, is stale: it is removed and the acquire retried once.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

LOCK_NAME = "computer-use.lock"
CORRUPT_GRACE_S = 30.0
_held: dict[str, int] = {}  # lock path → refcount (this process)
_mu = threading.Lock()


class LockBlocked(RuntimeError):
    def __init__(self, holder: dict[str, Any]) -> None:
        super().__init__(
            f"computer-use lock held by pid {holder.get('pid')} (session {holder.get('session_id') or '?'})"
        )
        self.holder = holder


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _create(path: Path, session_id: str) -> bool:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "session_id": session_id, "ts": time.time()}, f)
    return True


def _stale(path: Path, holder: dict[str, Any] | None) -> bool:
    if holder is None:
        try:
            return time.time() - path.stat().st_mtime > CORRUPT_GRACE_S
        except OSError:
            return True  # vanished: retry
    return not pid_alive(int(holder.get("pid") or 0))


def acquire(home: Path, session_id: str) -> str:
    """`fresh` | `reentrant` | `recovered` (a stale lock was replaced); raises LockBlocked."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / LOCK_NAME
    key = str(path)
    with _mu:
        for attempt in range(2):
            if _create(path, session_id):
                _held[key] = 1
                return "fresh" if attempt == 0 else "recovered"
            holder = _read(path)
            if holder and holder.get("pid") == os.getpid() and holder.get("session_id") == session_id:
                _held[key] = _held.get(key, 0) + 1
                return "reentrant"
            if attempt == 0 and _stale(path, holder):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                continue
            raise LockBlocked(holder or {"pid": None, "session_id": None})
    raise LockBlocked({"pid": None, "session_id": None})  # pragma: no cover


def release(home: Path, session_id: str) -> None:
    path = home / LOCK_NAME
    key = str(path)
    with _mu:
        n = _held.get(key, 0) - 1
        if n > 0:
            _held[key] = n
            return
        _held.pop(key, None)
        holder = _read(path)
        if holder and holder.get("pid") == os.getpid() and holder.get("session_id") == session_id:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


@contextlib.contextmanager
def held(home: Path, session_id: str) -> Iterator[str]:
    state = acquire(home, session_id)
    try:
        yield state
    finally:
        release(home, session_id)
