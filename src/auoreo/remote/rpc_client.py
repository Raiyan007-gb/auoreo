"""JSON-RPC-over-WebSocket client for `auoreo exec-server` (used by
`backends/environment/remote`). One connection, many in-flight calls (matched by `id`);
reconnects lazily after a drop — processes of a dropped connection are killed server-side.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from auoreo.remote.exec_server import PATH, is_loopback


class ExecRemoteError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class ExecClient:
    def __init__(self, url: str, token: str, *, timeout: float = 30.0) -> None:
        base = url.rstrip("/")
        self.url = base if base.endswith(PATH) else base + PATH
        self.token = token
        self.timeout = timeout
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    async def _connection(self) -> ClientConnection:
        async with self._lock:
            if self._ws is not None and self._reader is not None and not self._reader.done():
                return self._ws
            try:
                self._ws = await connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    open_timeout=self.timeout,
                    max_size=2**24,
                    proxy=None if is_loopback(urlsplit(self.url).hostname or "") else True,
                )
            except InvalidStatus as exc:
                code = exc.response.status_code
                raise ExecRemoteError(f"exec-server refused the connection (HTTP {code})", code) from exc
            except OSError as exc:
                raise ExecRemoteError(f"cannot reach exec-server at {self.url}: {exc}") from exc
            self._reader = asyncio.create_task(self._read(self._ws))
            return self._ws

    async def _read(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                msg = json.loads(raw)
                fut = self._pending.pop(msg.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except ConnectionClosed:
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ExecRemoteError("exec-server connection closed"))
            self._pending.clear()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        ws = await self._connection()
        rid = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        try:
            msg = await asyncio.wait_for(fut, timeout or self.timeout)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"] or {}
            raise ExecRemoteError(str(err.get("message", "error")), err.get("code"))
        return msg.get("result")

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        self._ws = self._reader = None
