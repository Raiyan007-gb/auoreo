"""Process bookkeeping shared by the non-local environment backends (W2-A).

A `StreamProc` is one command running somewhere else (a container, an SSH host, a cloud
sandbox): the backend's reader feeds it output and calls `finish(code)`; `collect()` waits
until exit or the yield deadline, like the local `ShellRegistry`. A `ProcTable` holds a
backend's live processes with the same per-session cap and globally unique ids.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from auoreo.backends.environment import ExecChunk
from auoreo.tools.shell_sessions import RING_BYTES, ShellError


@dataclass
class Chunk(ExecChunk):
    """`ExecChunk` plus what the renderer shows when a backend knows it."""

    pid: int | None = None
    tty: bool = False
    dropped: int = 0


class StreamProc:
    def __init__(self, proc_id: str, session_id: str, cmd: str, *, tty: bool = False) -> None:
        self.id = proc_id
        self.session_id = session_id
        self.cmd = cmd
        self.tty = tty
        self.buf = bytearray()
        self.base = 0
        self.consumed = 0
        self.done = False
        self.exit_code: int | None = None
        self.changed = asyncio.Event()
        self.last_used = time.monotonic()
        self.writer: Callable[[str], Awaitable[None]] | None = None
        self.killer: Callable[[], Awaitable[None]] | None = None
        self.tasks: list[asyncio.Task[None]] = []

    def feed(self, data: bytes | str) -> None:
        self.buf += data.encode() if isinstance(data, str) else data
        over = len(self.buf) - RING_BYTES
        if over > 0:
            del self.buf[:over]
            self.base += over
        self.changed.set()

    def finish(self, code: int | None) -> None:
        if not self.done:
            self.exit_code = code if code is not None else -1
            self.done = True
        self.changed.set()

    def take(self) -> tuple[str, int]:
        start = max(self.consumed, self.base)
        dropped = start - self.consumed
        data = bytes(self.buf[start - self.base :])
        self.consumed = self.base + len(self.buf)
        return data.decode("utf-8", errors="replace"), dropped

    def spawn(self, coro: Awaitable[None]) -> None:
        """Run a reader coroutine; an unexpected failure ends the process with code -1."""

        async def guarded() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.feed(f"\n[environment: output stream failed: {type(exc).__name__}: {exc}]\n")
                self.finish(-1)

        self.tasks.append(asyncio.create_task(guarded(), name=f"env:{self.id}"))

    async def collect(self, yield_s: float) -> Chunk:
        self.last_used = time.monotonic()
        deadline = time.monotonic() + yield_s
        while not self.done:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), min(left, 0.25))
        text, dropped = self.take()
        code = self.exit_code if self.done else None
        return Chunk(text, code, self.id, dropped > 0, tty=self.tty, dropped=dropped)

    async def kill(self) -> None:
        if self.killer is not None and not self.done:
            with contextlib.suppress(Exception):
                await self.killer()
        for t in self.tasks:
            t.cancel()
        self.finish(-9)


class ProcTable:
    """Live processes of one backend instance, keyed by globally unique id."""

    def __init__(self, backend: str, max_per_session: int = 8) -> None:
        self.backend = backend
        self.max_per_session = max_per_session
        self.procs: dict[str, StreamProc] = {}

    def live(self, session_id: str) -> list[StreamProc]:
        return [p for p in self.procs.values() if p.session_id == session_id]

    def new(self, session_id: str, cmd: str, *, tty: bool = False) -> StreamProc:
        """A fresh process record (raises `ShellError` past the per-session cap)."""
        mine = self.live(session_id)
        if len(mine) >= self.max_per_session:
            raise ShellError(
                f"too many live processes ({len(mine)}/{self.max_per_session}) in environment "
                f"'{self.backend}': {', '.join(sorted(p.id for p in mine))}. Finish one first"
            )
        pid = secrets.token_hex(3)
        while pid in self.procs:
            pid = secrets.token_hex(3)
        proc = StreamProc(pid, session_id, cmd, tty=tty)
        self.procs[pid] = proc
        return proc

    def get(self, proc_id: str) -> StreamProc:
        p = self.procs.get(proc_id)
        if p is None:
            raise ShellError(f"no running exec_command session '{proc_id}' in environment '{self.backend}'")
        return p

    async def collect(self, proc: StreamProc, yield_ms: int) -> Chunk:
        chunk = await proc.collect(yield_ms / 1000)
        if proc.done:
            self.procs.pop(proc.id, None)
        return chunk

    async def write(self, proc_id: str, chars: str, yield_ms: int) -> Chunk:
        p = self.get(proc_id)
        if chars:
            if p.done:
                raise ShellError(f"process {p.id} has exited (exit code {p.exit_code}); stdin is closed")
            if p.writer is None:
                raise ShellError(
                    f"the '{self.backend}' environment does not support stdin; poll with chars=''"
                )
            try:
                await p.writer(chars)
            except ShellError:
                raise
            except Exception as exc:
                raise ShellError(f"stdin of {p.id} is closed: {type(exc).__name__}: {exc}") from exc
        return await self.collect(p, yield_ms)

    async def close_session(self, session_id: str) -> None:
        for p in self.live(session_id):
            await p.kill()
            self.procs.pop(p.id, None)

    async def close_all(self) -> None:
        for p in list(self.procs.values()):
            await p.kill()
        self.procs.clear()
