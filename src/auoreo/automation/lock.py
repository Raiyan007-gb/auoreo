"""Cross-process single-writer lock: `flock(<home>/automation.lock)`.

The outbox worker and the scheduler of one Services share the lock (refcounted by owner);
a second server or a CLI process on the same home sees it taken and stays passive.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)
_HELD: dict[str, tuple[IO[bytes], int, int]] = {}  # path → (file, owner id, refcount)


def acquire(home: Path, owner: object) -> bool:
    path = str((home / "automation.lock").resolve())
    held = _HELD.get(path)
    if held is not None:
        fh, who, n = held
        if who != id(owner):
            return False
        _HELD[path] = (fh, who, n + 1)
        return True
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115 - kept open while held
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:  # no flock (Windows): in-process exclusion only
        pass
    except OSError:
        fh.close()
        log.info("automation lock %s is held by another process; staying passive", path)
        return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()).encode())
    fh.flush()
    _HELD[path] = (fh, id(owner), 1)
    return True


def release(home: Path, owner: object) -> None:
    path = str((home / "automation.lock").resolve())
    held = _HELD.get(path)
    if held is None or held[1] != id(owner):
        return
    fh, who, n = held
    if n > 1:
        _HELD[path] = (fh, who, n - 1)
        return
    del _HELD[path]
    fh.close()  # closing the descriptor drops the flock
