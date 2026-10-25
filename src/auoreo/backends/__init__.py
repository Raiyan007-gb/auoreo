"""Backend ABCs: one module per kind (`capture`, `browser`, `stt`, …).

Implementations live in `backends/<kind>/<name>/impl.py` folders and are discovered like
tools. Every backend class takes `Settings` in its constructor and implements
`async check() -> BackendStatus`; `close()` is optional.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendStatus:
    ok: bool
    detail: str = ""
    remedy: str = ""
