"""A self-contained local `EnvironmentBackend`: `/bin/sh -c` in its own process group, output
gathered until exit or `yield_time_ms`, later polled/fed through `write_stdin`.

The exec-server's default backend, so `auoreo exec-server` works standalone; inject any
other `EnvironmentBackend` (e.g. `backends/environment/local`) to serve it instead.
No pty: `tty=True` runs with pipes (reported in the first chunk).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import uuid
from dataclasses import dataclass, field

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecChunk, ExecRequest

READ_CHUNK = 65536
MAX_PROCESSES = 32


@dataclass
class _Proc:
    proc: asyncio.subprocess.Process
    session_id: str
    buf: bytearray = field(default_factory=bytearray)
    eof: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task[None] | None = None
    dropped: int = 0
    """Bytes discarded because the buffer exceeded its cap between polls."""


class LocalProcessBackend(EnvironmentBackend):
    name = "exec-local"
    remote = False

    def __init__(self, settings: object = None, *, buffer_cap: int = 4_000_000) -> None:
        super().__init__(settings)
        self.procs: dict[str, _Proc] = {}
        self.buffer_cap = buffer_cap

    async def check(self) -> BackendStatus:
        ok = await asyncio.to_thread(os.path.exists, "/bin/sh")
        return BackendStatus(ok, "local /bin/sh (no isolation)")

    async def _pump(self, p: _Proc) -> None:
        assert p.proc.stdout is not None
        try:
            while chunk := await p.proc.stdout.read(READ_CHUNK):
                p.buf.extend(chunk)
                if len(p.buf) > self.buffer_cap:
                    over = len(p.buf) - self.buffer_cap
                    del p.buf[:over]
                    p.dropped += over
        finally:
            p.eof.set()

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> ExecChunk:
        if len(self.procs) >= MAX_PROCESSES:
            raise RuntimeError(f"too many live processes (max {MAX_PROCESSES}); close a session first")
        workdir = req.workdir or os.getcwd()
        if not await asyncio.to_thread(os.path.isdir, workdir):
            raise ValueError(f"workdir does not exist: {workdir}")
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            req.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workdir,
            env=req.env,
            start_new_session=True,
        )
        pid = f"p_{uuid.uuid4().hex[:12]}"
        p = _Proc(proc, session_id)
        p.reader = asyncio.create_task(self._pump(p))
        self.procs[pid] = p
        chunk = await self._collect(pid, req.yield_time_ms, req.max_output_bytes)
        if req.tty:
            chunk.output = "[no pty: ran with pipes]\n" + chunk.output
        return chunk

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> ExecChunk:
        p = self.procs.get(process_id)
        if p is None:
            raise KeyError(f"unknown or finished process '{process_id}'")
        if chars and p.proc.stdin is not None and not p.proc.stdin.is_closing():
            p.proc.stdin.write(chars.encode())
            with contextlib.suppress(ConnectionError):
                await p.proc.stdin.drain()
        return await self._collect(process_id, yield_time_ms, 64_000)

    async def _collect(self, pid: str, yield_ms: int, max_bytes: int) -> ExecChunk:
        p = self.procs[pid]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(p.eof.wait(), max(yield_ms, 0) / 1000)
        exit_code: int | None = None
        if p.eof.is_set():
            exit_code = await p.proc.wait()
        data, p.buf = bytes(p.buf), bytearray()
        truncated = p.dropped > 0 or len(data) > max_bytes
        p.dropped = 0
        if len(data) > max_bytes:  # keep the tail: the end of a log is the useful part
            data = data[-max_bytes:]
        if exit_code is not None:
            self.procs.pop(pid, None)
        return ExecChunk(
            output=data.decode(errors="replace"),
            exit_code=exit_code,
            process_id=None if exit_code is not None else pid,
            truncated=truncated,
        )

    def _kill(self, pid: str) -> None:
        p = self.procs.pop(pid, None)
        if p is None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.proc.pid, signal.SIGKILL)
        if p.reader is not None:
            p.reader.cancel()

    async def close_session(self, session_id: str) -> None:
        for pid in [k for k, p in self.procs.items() if p.session_id == session_id]:
            self._kill(pid)

    async def close(self) -> None:
        for pid in list(self.procs):
            self._kill(pid)
