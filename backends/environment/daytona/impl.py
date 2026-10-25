"""Daytona environment over the REST API (`settings.environment_daytona_api_url`).

- `POST {api}/sandbox` (`{"snapshot"?, "labels", "autoStopInterval"}`) → `{"id", "state"}`;
  poll `GET {api}/sandbox/{id}` until `state == "started"`;
- `POST {api}/toolbox/{id}/toolbox/process/execute` (`{"command", "cwd"?, "timeout"}`) →
  `{"exitCode", "result"}` (stdout+stderr);
- `DELETE {api}/sandbox/{id}` at session close. `Authorization: Bearer <DAYTONA_API_KEY>`.

The execute call returns when the command ends, so it runs in the background: within
`yield_time_ms` the result comes back directly, otherwise the process is reported as running
and `write_stdin(chars="")` polls it. There is no stdin. Contract-tested only.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import time
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.secrets import SecretStore
from auoreo.tools.env_proc import Chunk, ProcTable, StreamProc
from auoreo.tools.shell_sessions import ShellError

KEY = "DAYTONA_API_KEY"
START_TIMEOUT_S = 120.0


class Backend(EnvironmentBackend):
    name = "daytona"
    remote = True
    poll_s = 1.0

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._sandboxes: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.table = ProcTable(self.name, int(getattr(settings, "shell_max_processes", 8) or 8))

    @property
    def api(self) -> str:
        return str(self.settings.environment_daytona_api_url).rstrip("/")

    def _key(self) -> str | None:
        return SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file")).get(KEY)

    def _headers(self) -> dict[str, str]:
        key = self._key()
        if not key:
            raise ShellError(f"{KEY} is not set; run `auoreo secrets set {KEY}`")
        return {"Authorization": f"Bearer {key}"}

    async def _call(self, method: str, path: str, *, timeout: float = 30.0, **kw: Any) -> Any:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.request(method, f"{self.api}{path}", headers=self._headers(), **kw)
        except httpx.HTTPError as exc:
            raise ShellError(f"daytona request failed: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise ShellError(f"daytona {method} {path.split('?')[0]}: HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def check(self) -> BackendStatus:
        if not self._key():
            return BackendStatus(False, f"{KEY} is not set", f"auoreo secrets set {KEY}")
        try:
            await self._call("GET", "/sandbox", timeout=8.0)
        except ShellError as exc:
            return BackendStatus(False, str(exc), f"check {KEY} and settings.environment_daytona_api_url")
        return BackendStatus(True, f"Daytona API reachable at {self.api} (key {KEY} present)")

    async def _sandbox(self, session_id: str) -> str:
        async with self._lock:
            if session_id in self._sandboxes:
                return self._sandboxes[session_id]
            body: dict[str, Any] = {
                "labels": {"auoreo_session": session_id},
                "autoStopInterval": max(1, self.settings.environment_sandbox_timeout_s // 60),
            }
            if snap := self.settings.environment_daytona_snapshot:
                body["snapshot"] = snap
            sbx = await self._call("POST", "/sandbox", json=body, timeout=START_TIMEOUT_S)
            sid = str(sbx["id"])
            self._sandboxes[session_id] = sid
            deadline = time.monotonic() + START_TIMEOUT_S
            while sbx.get("state", "started") != "started":
                if sbx.get("state") in ("error", "build_failed", "destroyed"):
                    raise ShellError(f"daytona sandbox {sid} failed to start (state {sbx.get('state')})")
                if time.monotonic() > deadline:
                    raise ShellError(f"daytona sandbox {sid} did not start within {START_TIMEOUT_S:.0f}s")
                await asyncio.sleep(self.poll_s)
                sbx = await self._call("GET", f"/sandbox/{sid}")
            return sid

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        sid = await self._sandbox(session_id)
        proc = self.table.new(session_id, req.cmd)
        body: dict[str, Any] = {
            "command": f"bash -lc {shlex.quote(req.cmd)}",
            "timeout": self.settings.environment_sandbox_timeout_s,
        }
        if req.workdir:
            body["cwd"] = req.workdir
        proc.spawn(self._run(proc, sid, body))
        return await self.table.collect(proc, req.yield_time_ms)

    async def _run(self, proc: StreamProc, sid: str, body: dict[str, Any]) -> None:
        out = await self._call(
            "POST", f"/toolbox/{sid}/toolbox/process/execute", json=body, timeout=float(body["timeout"]) + 30
        )
        proc.feed(str(out.get("result", "")))
        proc.finish(int(out.get("exitCode", -1)))

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        return await self.table.write(process_id, chars, yield_time_ms)

    async def close_session(self, session_id: str) -> None:
        await self.table.close_session(session_id)
        sid = self._sandboxes.pop(session_id, None)
        if sid is not None:
            with contextlib.suppress(ShellError):
                await self._call("DELETE", f"/sandbox/{sid}")

    async def close(self) -> None:
        for session_id in list(self._sandboxes):
            await self.close_session(session_id)
        await self.table.close_all()
