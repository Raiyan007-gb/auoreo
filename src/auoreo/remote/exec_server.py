"""`auoreo exec-server`: a small standalone WebSocket JSON-RPC server exposing a LOCAL
environment backend to a remote AUOREO (`backends/environment/remote`).

Separate from the main app on purpose: it has no sessions, no LLM, no event log — only the
execution plane (`exec_command`, `write_stdin`, `close_session`, `ping`).

Threat model (see docs/remote.md): whoever holds the token can run commands as this user.
So: the bearer token is checked at the handshake (constant time; 403 otherwise); a
non-loopback bind needs `--allow-remote` AND a token; the hardline `bash` floors are
re-checked HERE, so a compromised client still cannot `rm -rf /`; commands get a scrubbed
environment; every exec is logged with a `cmd_hash` (never the command text).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from auoreo import __version__
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.permissions import PermissionEngine
from auoreo.core.secrets import child_env
from auoreo.remote.exec_local import LocalProcessBackend

log = logging.getLogger("auoreo.exec_server")
PATH = "/rpc"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ENV_DENY = ("LD_", "DYLD_", "PYTHONSTARTUP", "BASH_ENV", "ENV", "PROMPT_COMMAND", "IFS")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
DENIED, EXEC_FAILED = -32001, -32002


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def cmd_hash(cmd: str) -> str:
    return hashlib.sha256(cmd.encode()).hexdigest()[:16]


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, *, allow_remote: bool, token: str | None) -> None:
    """Refuse an unauthenticated server and any non-loopback bind without explicit opt-in."""
    if not token:
        raise ValueError("the exec-server needs a token (--token-secret secret:<name> or AUOREO_EXEC_TOKEN)")
    if not is_loopback(host) and not allow_remote:
        raise ValueError(f"refusing to bind non-loopback {host} without --allow-remote (and a token)")


def scrub_env(extra: dict[str, Any] | None) -> dict[str, str]:
    """Allowlisted host env (`core.secrets.child_env`) + caller vars minus loader hooks."""
    env = child_env([], None)
    for k, v in (extra or {}).items():
        if isinstance(v, str) and _ENV_NAME.match(k) and not k.startswith(_ENV_DENY):
            env[k] = v
    return env


@dataclass
class _Conn:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    sessions: set[str] = field(default_factory=set)
    processes: set[str] = field(default_factory=set)

    def scoped(self, session_id: str) -> str:
        """Backend session key: a client can only touch sessions it opened."""
        key = f"{self.id}:{session_id}"
        self.sessions.add(key)
        return key


class ExecServer:
    def __init__(
        self,
        token: str,
        backend: EnvironmentBackend | None = None,
        *,
        floors: list[str] | None = None,
        default_workdir: str | None = None,
    ) -> None:
        if not token:
            raise ValueError("token required")
        self.token = token
        self.backend = backend or LocalProcessBackend()
        self.engine = PermissionEngine(rules=[], floors=list(floors or []))
        self.default_workdir = default_workdir
        self.methods: dict[str, Callable[[_Conn, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "ping": self._ping,
            "exec_command": self._exec,
            "write_stdin": self._write_stdin,
            "close_session": self._close_session,
        }

    def authorized(self, header: str | None) -> bool:
        if not header or not header.lower().startswith("bearer "):
            return False
        return secrets.compare_digest(header[7:].strip().encode(), self.token.encode())

    # ----------------------------------------------------------------- methods
    async def _ping(self, conn: _Conn, params: dict[str, Any]) -> dict[str, Any]:
        st = await self.backend.check()
        return {"ok": st.ok, "detail": st.detail, "backend": self.backend.name, "version": __version__}

    async def _exec(self, conn: _Conn, params: dict[str, Any]) -> dict[str, Any]:
        cmd = params.get("cmd")
        session_id = params.get("session_id") or "default"
        if not isinstance(cmd, str) or not cmd.strip() or not isinstance(session_id, str):
            raise RpcError(INVALID_PARAMS, "exec_command needs a non-empty string 'cmd'")
        digest = cmd_hash(cmd)
        if (why := self.engine.floor("bash", cmd, interactive=False)) is not None:
            log.warning(
                "exec denied cmd_hash=%s conn=%s session=%s reason=%s", digest, conn.id, session_id, why
            )
            raise RpcError(DENIED, f"refused by the exec-server: {why}")
        tokens = int(params.get("max_output_tokens") or 16_000)
        req = ExecRequest(
            cmd=cmd,
            workdir=params.get("workdir") or self.default_workdir,
            tty=bool(params.get("tty", False)),
            yield_time_ms=max(0, min(int(params.get("yield_time_ms", 10_000)), 300_000)),
            max_output_bytes=max(256, min(tokens * 4, 4_000_000)),
            env=scrub_env(params.get("env")),
        )
        log.info("exec cmd_hash=%s conn=%s session=%s workdir=%s", digest, conn.id, session_id, req.workdir)
        chunk = await self.backend.exec_command(req, session_id=conn.scoped(session_id))
        if chunk.process_id:
            conn.processes.add(chunk.process_id)
        return asdict(chunk)

    async def _write_stdin(self, conn: _Conn, params: dict[str, Any]) -> dict[str, Any]:
        pid = params.get("process_id")
        if not isinstance(pid, str) or pid not in conn.processes:
            raise RpcError(INVALID_PARAMS, f"unknown process '{pid}' for this connection")
        chars = params.get("chars") or ""
        if not isinstance(chars, str):
            raise RpcError(INVALID_PARAMS, "'chars' must be a string")
        yield_ms = max(0, min(int(params.get("yield_time_ms", 1_000)), 300_000))
        chunk = await self.backend.write_stdin(pid, chars, yield_time_ms=yield_ms)
        if chunk.process_id is None:
            conn.processes.discard(pid)
        return asdict(chunk)

    async def _close_session(self, conn: _Conn, params: dict[str, Any]) -> dict[str, Any]:
        key = f"{conn.id}:{params.get('session_id') or 'default'}"
        if key in conn.sessions:
            await self.backend.close_session(key)
            conn.sessions.discard(key)
        return {"ok": True}

    # ----------------------------------------------------------------- transport
    async def dispatch(self, conn: _Conn, raw: str) -> dict[str, Any] | None:
        rid: Any = None
        try:
            try:
                msg = json.loads(raw)
            except ValueError as exc:
                raise RpcError(PARSE_ERROR, "invalid JSON") from exc
            if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
                raise RpcError(INVALID_REQUEST, "expected a JSON-RPC 2.0 request object")
            rid = msg.get("id")
            handler = self.methods.get(msg["method"])
            if handler is None:
                raise RpcError(METHOD_NOT_FOUND, f"unknown method '{msg['method']}'")
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "params must be an object")
            result = await handler(conn, params)
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": exc.code, "message": str(exc)}}
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            msg_text = str(exc).strip("'\"")
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": EXEC_FAILED, "message": msg_text}}

    async def endpoint(self, ws: WebSocket) -> None:
        if not self.authorized(ws.headers.get("authorization")):
            log.warning("exec-server: rejected handshake from %s (bad or missing token)", ws.client)
            await ws.close(code=1008)  # before accept → uvicorn answers HTTP 403
            return
        await ws.accept()
        conn, lock, tasks = _Conn(), asyncio.Lock(), set[asyncio.Task[None]]()

        async def handle(raw: str) -> None:
            reply = await self.dispatch(conn, raw)
            if reply is not None:
                async with lock:
                    await ws.send_text(json.dumps(reply))

        try:
            while True:
                task = asyncio.create_task(handle(await ws.receive_text()))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            for t in list(tasks):
                t.cancel()
            for key in list(conn.sessions):  # a dropped client's processes do not linger
                await self.backend.close_session(key)

    def app(self) -> Starlette:
        @asynccontextmanager
        async def lifespan(_: Starlette) -> AsyncIterator[None]:
            try:
                yield
            finally:
                await self.backend.close()

        return Starlette(routes=[WebSocketRoute(PATH, self.endpoint)], lifespan=lifespan)
