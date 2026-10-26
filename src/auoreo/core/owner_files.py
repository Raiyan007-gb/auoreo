"""Owner-only key files (spec §24.9 L881, §25.14 L1060): `secrets.db`, `.secrets.key`, `.db.key`.

Writes create a fresh temp file with `os.open(O_CREAT|O_EXCL, 0o600)` (never a permissive
window), fsync it and atomically `os.replace` it over the target. Reads `fstat` the open
descriptor on POSIX: a file owned by another user is refused (`OwnerOnlyError`); group/other
permission bits are a warning and are repaired to `0600` in place.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger(__name__)
POSIX = os.name == "posix"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class OwnerOnlyError(PermissionError):
    """A key file is owned by someone else: never trust (or rewrite) it."""


def write_owner_only(path: Path, data: bytes) -> None:
    """Atomically replace `path` with `data`, created `0600` from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def check_owner_only(fd: int, path: Path) -> None:
    """POSIX: refuse a foreign owner; repair group/other bits with a warning."""
    if not POSIX:
        return
    st = os.fstat(fd)
    if st.st_uid != os.geteuid():
        raise OwnerOnlyError(f"{path} is owned by uid {st.st_uid}, not {os.geteuid()}: refusing to use it")
    if st.st_mode & 0o077:
        log.warning(
            "%s had mode %o; repairing to 0600 (key files must be owner-only)", path, st.st_mode & 0o777
        )
        os.fchmod(fd, 0o600)


def read_owner_only(path: Path) -> bytes:
    """Read a key file after `check_owner_only` on the very descriptor that is read."""
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    try:
        check_owner_only(fd, path)
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read()
    finally:
        os.close(fd)
