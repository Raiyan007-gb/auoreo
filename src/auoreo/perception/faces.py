"""In-memory face embedding vault for `detect_faces` (spec L744).

Embeddings never reach the transcript, the events table, memory or any file: the tool keeps
them here under an opaque `ref` for `TTL_S` and reports only refs and cosine similarities.
A session's refs are forgotten when it is archived/deleted (bus events via `SessionScoped`),
on expiry, on `forget=true`, and when the server stops.
"""

from __future__ import annotations

import math
import secrets
import time
from typing import Any

from auoreo.tools.shell_sessions import SessionScoped, attached

TTL_S = 10 * 60.0
MAX_PER_SESSION = 64


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class FaceVault(SessionScoped):
    def __init__(self, services: Any, ttl_s: float = TTL_S) -> None:
        super().__init__(services)
        self.ttl_s = ttl_s
        self._store: dict[str, dict[str, tuple[float, list[float]]]] = {}

    def put(self, session_id: str, embedding: list[float]) -> str:
        self.ensure_tasks()
        mine = self._store.setdefault(session_id, {})
        self._expire(mine)
        while len(mine) >= MAX_PER_SESSION:
            mine.pop(min(mine, key=lambda k: mine[k][0]))
        ref = f"face_{secrets.token_hex(4)}"
        mine[ref] = (time.monotonic(), list(embedding))
        return ref

    def get(self, session_id: str, ref: str) -> list[float] | None:
        mine = self._store.get(session_id, {})
        self._expire(mine)
        item = mine.get(ref)
        return item[1] if item else None

    def count(self, session_id: str) -> int:
        return len(self._store.get(session_id, {}))

    def _expire(self, mine: dict[str, tuple[float, list[float]]]) -> None:
        now = time.monotonic()
        for ref in [r for r, (t, _) in mine.items() if now - t > self.ttl_s]:
            mine.pop(ref, None)

    async def close_session(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def reap_idle(self) -> None:
        for mine in self._store.values():
            self._expire(mine)

    async def close_all(self) -> None:
        self._store.clear()


def vault_for(services: Any) -> FaceVault:
    return attached(services, "face_vault", FaceVault)
