"""Host side of the plugin wire protocol: JSON-RPC 2.0, one JSON object per line on stdio.

host → plugin requests: `initialize`, `tool.call`, `delivery.send`, `connector.start|stop`,
`config.update`, `shutdown`. plugin → host: `connector.message` and `log` notifications,
`secret.get` and `pairing.request` requests (answered by the host's `on_request`).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]
NotifyHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
ExitHandler = Callable[[int | None], Awaitable[None]]


class RpcError(Exception):
    """A JSON-RPC error answer, a timeout, or a dead peer."""

    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


class RpcPeer:
    """One plugin subprocess. `request()` never raises anything but `RpcError`."""

    def __init__(
        self,
        name: str,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
        on_request: RequestHandler,
        on_notify: NotifyHandler,
        on_exit: ExitHandler | None = None,
    ) -> None:
        self.name = name
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.on_request = on_request
        self.on_notify = on_notify
        self.on_exit = on_exit
        self.proc: asyncio.subprocess.Process | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self.closing = False
        self.stderr_tail: list[str] = []

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=str(self.cwd),
            limit=32 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read_loop(), name=f"plugin:{self.name}:stdout")
        self._stderr = asyncio.create_task(self._stderr_loop(), name=f"plugin:{self.name}:stderr")

    # ----------------------------------------------------------------- wire
    async def _write(self, obj: dict[str, Any]) -> None:
        if not self.alive or self.proc is None or self.proc.stdin is None:
            raise RpcError(f"plugin '{self.name}' is not running")
        data = (json.dumps(obj, default=str) + "\n").encode()
        try:
            async with self._write_lock:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise RpcError(f"plugin '{self.name}' pipe closed: {exc}") from exc

    async def request(self, method: str, params: dict[str, Any], timeout: float = 60.0) -> Any:
        rid = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            raise RpcError(f"plugin '{self.name}' did not answer {method} within {timeout:g}s") from exc
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------- readers
    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while line := await self.proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("plugin %s wrote a non-JSON line: %r", self.name, line[:200])
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except (ValueError, asyncio.LimitOverrunError):
            log.warning("plugin %s wrote an oversized line; dropping the connection", self.name)
        finally:
            await self._exited()

    async def _stderr_loop(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while line := await self.proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            self.stderr_tail = [*self.stderr_tail[-19:], text]
            log.debug("plugin %s: %s", self.name, text)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "method" not in msg:
            fut = self._pending.get(msg.get("id"))  # type: ignore[arg-type]
            if fut is None or fut.done():
                return
            err = msg.get("error")
            if err:
                fut.set_exception(RpcError(str(err.get("message", "error")), int(err.get("code", -32000))))
            else:
                fut.set_result(msg.get("result"))
            return
        task = asyncio.create_task(self._incoming(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _incoming(self, msg: dict[str, Any]) -> None:
        method, params, rid = str(msg["method"]), msg.get("params") or {}, msg.get("id")
        if rid is None:
            try:
                await self.on_notify(method, params)
            except Exception:
                log.exception("plugin %s: notification %s failed", self.name, method)
            return
        try:
            reply: dict[str, Any] = {"result": await self.on_request(method, params)}
        except RpcError as exc:
            reply = {"error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            log.exception("plugin %s: request %s failed", self.name, method)
            reply = {"error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}}
        with contextlib.suppress(RpcError):
            await self._write({"jsonrpc": "2.0", "id": rid, **reply})

    async def _exited(self) -> None:
        code: int | None = None
        if self.proc is not None:
            with contextlib.suppress(Exception):
                code = await asyncio.wait_for(self.proc.wait(), 5)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcError(f"plugin '{self.name}' exited (code {code})"))
        if self.on_exit is not None and not self.closing:
            await self.on_exit(code)

    # ------------------------------------------------------------ shutdown
    async def close(self, timeout: float = 3.0) -> None:
        """`shutdown` request, then terminate, then kill."""
        self.closing = True
        if self.alive:
            with contextlib.suppress(RpcError):
                await self.request("shutdown", {}, timeout=timeout)
        proc = self.proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        for task in (self._reader, self._stderr, *self._tasks):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
