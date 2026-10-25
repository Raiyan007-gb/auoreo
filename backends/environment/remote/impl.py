"""Remote environment: the client side of `auoreo exec-server` (`src/auoreo/remote/`)."""

from __future__ import annotations

from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecChunk, ExecRequest
from auoreo.core.secrets import SecretStore
from auoreo.remote.rpc_client import ExecClient, ExecRemoteError


def _chunk(result: dict[str, Any]) -> ExecChunk:
    return ExecChunk(
        output=str(result.get("output", "")),
        exit_code=result.get("exit_code"),
        process_id=result.get("process_id"),
        truncated=bool(result.get("truncated", False)),
    )


class Backend(EnvironmentBackend):
    name = "remote"
    remote = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._client: ExecClient | None = None

    def _config(self) -> Any:
        cfg = getattr(self.settings, "environment_remote", None)
        if cfg is None:
            raise ExecRemoteError(
                "settings.environment_remote is not configured ({url, token: 'secret:<name>'})"
            )
        return cfg

    def client(self) -> ExecClient:
        if self._client is None:
            cfg = self._config()
            store = SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file"))
            token = store.get(cfg.token.removeprefix("secret:"))
            if not token:
                raise ExecRemoteError(f"secret '{cfg.token}' is not set")
            self._client = ExecClient(cfg.url, token, timeout=cfg.timeout_s)
        return self._client

    async def check(self) -> BackendStatus:
        try:
            pong = await self.client().call("ping", timeout=5)
        except (ExecRemoteError, TimeoutError) as exc:
            return BackendStatus(
                False, str(exc) or "ping timed out", "start `auoreo exec-server` on the host"
            )
        return BackendStatus(
            bool(pong.get("ok")), f"exec-server {pong.get('version')} ({pong.get('backend')})"
        )

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> ExecChunk:
        params = {
            "session_id": session_id,
            "cmd": req.cmd,
            "workdir": req.workdir,
            "tty": req.tty,
            "yield_time_ms": req.yield_time_ms,
            "max_output_tokens": max(1, req.max_output_bytes // 4),
            "env": req.env,
        }
        timeout = self._config().timeout_s + req.yield_time_ms / 1000
        return _chunk(await self.client().call("exec_command", params, timeout=timeout))

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> ExecChunk:
        params = {"process_id": process_id, "chars": chars, "yield_time_ms": yield_time_ms}
        timeout = self._config().timeout_s + yield_time_ms / 1000
        return _chunk(await self.client().call("write_stdin", params, timeout=timeout))

    async def close_session(self, session_id: str) -> None:
        if self._client is not None:
            await self._client.call("close_session", {"session_id": session_id})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
