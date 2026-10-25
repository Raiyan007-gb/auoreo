"""Local environment: wraps the persistent-shell registry (`auoreo.tools.shell_sessions`).

Bound to `Services` by the router, it shares the registry the server closes on shutdown
(and that watches session archive/delete); unbound (a bare `Backend(settings)`) it keeps
its own. It never bypasses an enforcing sandbox: `preflight` refuses unless the selected
sandbox backend is `none`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.tools.env_proc import Chunk
from auoreo.tools.shell_sessions import (
    ShellError,
    ShellProcess,
    ShellRegistry,
    registry_for,
    sandbox_refusal,
)


class Backend(EnvironmentBackend):
    name = "local"
    remote = False
    host_paths = True
    """`ExecRequest.workdir` is a host path (the router checks it against cwd)."""

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.services: Any = None
        self._own: ShellRegistry | None = None

    def bind(self, services: Any) -> None:
        self.services = services

    @property
    def registry(self) -> ShellRegistry:
        if self.services is not None:
            return registry_for(self.services)
        if self._own is None:
            cap = int(getattr(self.settings, "shell_max_processes", 8) or 8)
            self._own = ShellRegistry(None, max_processes=cap)
        return self._own

    def preflight(self, tool: str) -> None:
        if self.services is None:
            return
        if why := sandbox_refusal(self.services.backend("sandbox"), tool):
            raise ShellError(why)

    async def check(self) -> BackendStatus:
        if shutil.which("bash") is None:
            return BackendStatus(False, "bash not found on PATH", "install bash")
        return BackendStatus(True, "persistent local shell (unsandboxed; refused under an enforcing sandbox)")

    @staticmethod
    def _chunk(p: ShellProcess, text: str, dropped: int) -> Chunk:
        code = p.exit_code if p.finished else None
        if p.finished and code is None:
            code = -1
        return Chunk(text, code, p.id, dropped > 0, pid=p.pid, tty=p.tty, dropped=dropped)

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        self.preflight("exec_command")
        reg = self.registry
        p = await reg.start(
            session_id=session_id,
            cmd=req.cmd,
            cwd=Path(req.workdir) if req.workdir else Path.cwd(),
            env=dict(req.env),
            tty=req.tty,
            granted=req.cmd,
        )
        text, dropped = await reg.collect(p, req.yield_time_ms / 1000)
        return self._chunk(p, text, dropped)

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        self.preflight("write_stdin")
        reg = self.registry
        p = reg.find(process_id)
        if p is None:
            raise ShellError(f"no running exec_command session '{process_id}'")
        await reg.write(p, chars)
        text, dropped = await reg.collect(p, yield_time_ms / 1000)
        return self._chunk(p, text, dropped)

    async def close_session(self, session_id: str) -> None:
        await self.registry.close_session(session_id)

    async def close(self) -> None:
        if self._own is not None:  # a bound registry is closed with `services.ext`
            await self._own.close()
            self._own = None
