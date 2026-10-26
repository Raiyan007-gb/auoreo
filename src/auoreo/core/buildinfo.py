"""`code_sha`: a fingerprint of the code this process runs (spec §18/§20.3 doctor row).

`GET /api/health` reports it and `auoreo doctor` compares the running server's value with
its own, so a server started from an older checkout (or another venv) is caught.
`AUOREO_CODE_SHA` (set by release builds) wins; otherwise it is a SHA-256 over the package's
`.py` sources, so uncommitted edits count too."""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def code_sha() -> str:
    if env := os.environ.get("AUOREO_CODE_SHA", "").strip():
        return env[:40]
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(path.relative_to(PACKAGE_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()[:16]
