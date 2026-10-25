"""E2B environment: one cloud sandbox per session.

Lifecycle and commands use the `e2b` SDK (`AsyncSandbox.create(template=, api_key=,
timeout=)`, `sandbox.commands.run(cmd, background=True, stdin=True, on_stdout=,
on_stderr=, timeout=0)` → handle with `pid`/`wait()`/`kill()`, `commands.send_stdin(pid,
data)`, `sandbox.kill()`), because process streaming runs over envd's RPC, not the REST
API. `check()` uses the documented REST API (`GET https://api.e2b.app/sandboxes`,
`X-API-Key`) so it works without the SDK. Contract-tested only.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.secrets import SecretStore
from auoreo.tools.env_proc import Chunk, ProcTable, StreamProc
from auoreo.tools.shell_sessions import ShellError

KEY = "E2B_API_KEY"
API = "https://api.e2b.app"


def _sdk() -> Any:
    import e2b

    return e2b


class Backend(EnvironmentBackend):
    name = "e2b"
    remote = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._sandboxes: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.table = ProcTable(self.name, int(getattr(settings, "shell_max_processes", 8) or 8))

    def _key(self) -> str | None:
        return SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file")).get(KEY)

    async def check(self) -> BackendStatus:
        key = self._key()
        if not key:
            return BackendStatus(False, f"{KEY} is not set", f"auoreo secrets set {KEY}")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{API}/sandboxes", headers={"X-API-Key": key})
        except httpx.HTTPError as exc:
            return BackendStatus(False, f"E2B API unreachable ({type(exc).__name__})", "check the network")
        if r.status_code in (401, 403):
            return BackendStatus(
                False, f"E2B rejected {KEY} (HTTP {r.status_code})", f"auoreo secrets set {KEY}"
            )
        if r.status_code >= 400:
            return BackendStatus(False, f"E2B API error HTTP {r.status_code}", "retry later")
        try:
            _sdk()
        except ImportError:
            return BackendStatus(
                False, "E2B API reachable, but the `e2b` SDK is not installed", "pip install e2b"
            )
        return BackendStatus(
            True, f"E2B API reachable (key {KEY} present, template {self.settings.environment_e2b_template})"
        )

    async def _sandbox(self, session_id: str) -> Any:
        async with self._lock:
            if session_id not in self._sandboxes:
                key = self._key()
                if not key:
                    raise ShellError(f"{KEY} is not set; run `auoreo secrets set {KEY}`")
                try:
                    sdk = _sdk()
                except ImportError:
                    raise ShellError("the e2b environment needs the `e2b` SDK (pip install e2b)") from None
                self._sandboxes[session_id] = await sdk.AsyncSandbox.create(
                    template=self.settings.environment_e2b_template,
                    api_key=key,
                    timeout=self.settings.environment_sandbox_timeout_s,
                    metadata={"auoreo_session": session_id},
                )
            return self._sandboxes[session_id]

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        sbx = await self._sandbox(session_id)
        proc = self.table.new(session_id, req.cmd, tty=False)
        try:
            handle = await sbx.commands.run(
                req.cmd, background=True, stdin=True, cwd=req.workdir or None,
                on_stdout=proc.feed, on_stderr=proc.feed, timeout=0,
            )  # fmt: skip
        except BaseException:
            self.table.procs.pop(proc.id, None)
            raise

        async def write(chars: str) -> None:
            await sbx.commands.send_stdin(handle.pid, chars)

        async def kill() -> None:
            await handle.kill()

        proc.writer, proc.killer = write, kill
        proc.spawn(self._wait(proc, handle))
        return await self.table.collect(proc, req.yield_time_ms)

    @staticmethod
    async def _wait(proc: StreamProc, handle: Any) -> None:
        try:
            result = await handle.wait()
            code = getattr(result, "exit_code", 0)
        except Exception as exc:  # CommandExitException carries the non-zero exit code
            code = getattr(exc, "exit_code", None)
            if code is None:
                raise
        proc.finish(int(code))

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        return await self.table.write(process_id, chars, yield_time_ms)

    async def close_session(self, session_id: str) -> None:
        await self.table.close_session(session_id)
        sbx = self._sandboxes.pop(session_id, None)
        if sbx is not None:
            with contextlib.suppress(Exception):
                await sbx.kill()

    async def close(self) -> None:
        for sid in list(self._sandboxes):
            await self.close_session(sid)
        await self.table.close_all()
