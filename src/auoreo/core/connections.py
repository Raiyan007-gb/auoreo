"""Registry of live WebSocket control-plane connections (spec §5.2).

The API layer owns the socket; this module only tracks what is open so shutdown can
close every connection with 1001 (going away) before the database goes down.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Closable(Protocol):
    conn_id: str

    async def close(self, code: int, reason: str = "") -> None: ...


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[str, Closable] = {}
        self.binary_dropped = 0
        """Binary frames received and dropped across all connections (metric)."""
        self.total_opened = 0

    def add(self, conn: Closable) -> None:
        self._conns[conn.conn_id] = conn
        self.total_opened += 1

    def remove(self, conn: Closable) -> None:
        self._conns.pop(conn.conn_id, None)

    def __len__(self) -> int:
        return len(self._conns)

    def ids(self) -> list[str]:
        return list(self._conns)

    async def close_all(self, code: int = 1001, reason: str = "server shutting down") -> None:
        conns = list(self._conns.values())
        self._conns.clear()
        for conn in conns:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.close(code, reason), timeout=2.0)
        if conns:
            log.info("closed %d websocket connection(s) with %d", len(conns), code)
