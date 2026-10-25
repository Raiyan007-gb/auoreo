"""Modal environment: one `modal.Sandbox` per session (Modal has no public REST API).

`client = modal.Client.from_credentials(id, secret)`, `app = await modal.App.lookup.aio(name,
create_if_missing=True, client=)`, `sb = await modal.Sandbox.create.aio(app=, image=
modal.Image.from_registry(...), timeout=, client=)`, `p = await sb.exec.aio("bash", "-lc",
cmd, workdir=)`; output from `p.stdout` / `p.stderr` (async iterators), stdin with
`p.stdin.write(b)` + `await p.stdin.drain.aio()`, exit from `await p.wait.aio()`, and
`await sb.terminate.aio()` at session close. Contract-tested only.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.secrets import SecretStore
from auoreo.tools.env_proc import Chunk, ProcTable, StreamProc
from auoreo.tools.shell_sessions import ShellError

KEYS = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")


def _sdk() -> Any:
    import modal

    return modal


class Backend(EnvironmentBackend):
    name = "modal"
    remote = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._client: Any = None
        self._sandboxes: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.table = ProcTable(self.name, int(getattr(settings, "shell_max_processes", 8) or 8))

    def _tokens(self) -> tuple[str | None, str | None]:
        store = SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file"))
        return store.get(KEYS[0]), store.get(KEYS[1])

    async def check(self) -> BackendStatus:
        missing = [k for k, v in zip(KEYS, self._tokens(), strict=True) if not v]
        if missing:
            return BackendStatus(False, f"{', '.join(missing)} not set", f"auoreo secrets set {missing[0]}")
        try:
            _sdk()
        except ImportError:
            return BackendStatus(False, "the `modal` SDK is not installed", "pip install modal")
        return BackendStatus(
            True,
            f"Modal tokens present, SDK installed (not contacted; {self.settings.environment_modal_image})",
        )

    async def _sandbox(self, session_id: str) -> Any:
        async with self._lock:
            if session_id in self._sandboxes:
                return self._sandboxes[session_id]
            token_id, token_secret = self._tokens()
            if not (token_id and token_secret):
                raise ShellError(f"{' and '.join(KEYS)} must be set (auoreo secrets set …)")
            try:
                modal = _sdk()
            except ImportError:
                raise ShellError("the modal environment needs the `modal` SDK (pip install modal)") from None
            if self._client is None:
                self._client = modal.Client.from_credentials(token_id, token_secret)
            st = self.settings
            app = await modal.App.lookup.aio(
                st.environment_modal_app, create_if_missing=True, client=self._client
            )
            sb = await modal.Sandbox.create.aio(
                app=app,
                image=modal.Image.from_registry(st.environment_modal_image),
                timeout=st.environment_sandbox_timeout_s,
                client=self._client,
            )
            self._sandboxes[session_id] = sb
            return sb

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        sb = await self._sandbox(session_id)
        proc = self.table.new(session_id, req.cmd, tty=False)
        try:
            kwargs: dict[str, Any] = {"workdir": req.workdir} if req.workdir else {}
            p = await sb.exec.aio("bash", "-lc", req.cmd, **kwargs)
        except BaseException:
            self.table.procs.pop(proc.id, None)
            raise

        async def write(chars: str) -> None:
            p.stdin.write(chars.encode())
            await p.stdin.drain.aio()

        proc.writer = write
        proc.spawn(self._pump(proc, p))
        return await self.table.collect(proc, req.yield_time_ms)

    @staticmethod
    async def _pump(proc: StreamProc, p: Any) -> None:
        async def drain(stream: Any) -> None:
            async for chunk in stream:
                proc.feed(chunk)

        await asyncio.gather(drain(p.stdout), drain(p.stderr))
        proc.finish(await p.wait.aio())

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        return await self.table.write(process_id, chars, yield_time_ms)

    async def close_session(self, session_id: str) -> None:
        await self.table.close_session(session_id)
        sb = self._sandboxes.pop(session_id, None)
        if sb is not None:
            with contextlib.suppress(Exception):
                await sb.terminate.aio()

    async def close(self) -> None:
        for sid in list(self._sandboxes):
            await self.close_session(sid)
        await self.table.close_all()
