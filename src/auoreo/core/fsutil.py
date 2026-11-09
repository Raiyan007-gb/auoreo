"""Small file helpers shared by `core` and the optional subsystems."""

from __future__ import annotations

import os
from pathlib import Path


def write_private(target: Path, text: str) -> None:
    """Atomic write of a 0600 file (the tmp file is created 0600, then renamed over). On
    Windows the mode is a no-op: the file is protected by the user-profile ACL only."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    tmp.replace(target)
