"""I9: holder lock + schema guard for `auoreo upgrade` / `--rollback` (spec §25.15 L1096).

- `holder_lock(home)`: one upgrade or rollback at a time per home (multiplex profiles that share
  a binary each take their own home's lock *and* the binary's lock). POSIX `flock`, else an
  `O_EXCL` lock file whose stale owner (dead pid) is reclaimed. Never kills anything.
- `db_schema(home)`: the `schema_version` recorded in `<home>/state.db` (plain sqlite3,
  read-only); None when there is no database or it cannot be read (e.g. SQLCipher).
- `rollback_refusal(...)`: a rollback to a binary whose schema is older than the database the
  newer release already migrated is refused unless forced — the old binary would refuse to
  open it ("schema newer than this build").
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path


class LockHeld(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: O_EXCL lock file with stale-pid recovery
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                try:
                    pid = int(path.read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _pid_alive(pid):
                    raise LockHeld(f"{path} is held by pid {pid}") from None
                with contextlib.suppress(OSError):
                    path.unlink()
        else:  # pragma: no cover - lost the race twice
            raise LockHeld(f"{path} is held")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                path.unlink()
        return
    fh = path.open("a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            raise LockHeld(f"{path} is held by pid {fh.read().strip() or '?'}") from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        fh.close()


@contextlib.contextmanager
def holder_lock(home: Path, binary: Path | None = None) -> Iterator[None]:
    """Exclusive upgrade/rollback lock for `home` (and the shared `binary`, when given)."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(_exclusive(home / "upgrade" / ".lock"))
        if binary is not None:
            stack.enter_context(_exclusive(binary.with_name(f".{binary.name}.upgrade-lock")))
        yield


def db_schema(home: Path) -> int | None:
    path = home / "state.db"
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def rollback_refusal(receipt: dict[str, object], home: Path, *, force: bool = False) -> str | None:
    """Why rolling back to the receipt's `from` build is unsafe, or None."""
    prev = receipt.get("from_schema")
    current = db_schema(home)
    if force or not isinstance(prev, int) or current is None or current <= prev:
        return None
    return (
        f"state.db is at schema v{current} but the previous build ({receipt.get('from', '?')}) only "
        f"knows v{prev}: it would refuse to open the database. Restore a pre-upgrade backup of "
        "state.db first (the guard is skipped only when called with force=True)"
    )
