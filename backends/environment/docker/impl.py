"""Docker environment: a long-lived container per session, commands via `docker exec`.

The container (`sleep infinity` under `--init`) is created lazily on the first command;
the session's working directory is mounted at `/work` (fixed at creation: later cwd changes
do not remount). Never privileged; all capabilities dropped; `no-new-privileges`; uid 1000.
`exec_start(socket=True)` gives a raw socket: stdin is written to it and output read from
it (non-tty output is Docker's 8-byte-header multiplexed stream). The Docker SDK is
imported lazily (extra `sandbox-docker`).
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.tools.env_proc import Chunk, ProcTable, StreamProc

WORKDIR = "/work"
PING_TIMEOUT_S = 3.0
REMEDY = (
    "start the Docker daemon (or set DOCKER_HOST) and install the extra "
    "(`uv sync --extra sandbox-docker`), or pick another environment"
)
_HOST_ONLY_ENV = frozenset({"PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "SHELL", "PWD"})


def _from_env(**kwargs: Any) -> Any:
    import docker

    return docker.from_env(**kwargs)


def container_kwargs(settings: Any, session_id: str, root: Path) -> dict[str, Any]:
    """Arguments for `client.containers.run` (pure; unit-tested)."""
    return {
        "image": settings.environment_docker_image,
        "command": ["sleep", "infinity"],
        "init": True,
        "detach": True,
        "working_dir": WORKDIR,
        "volumes": {str(root): {"bind": WORKDIR, "mode": "rw"}},
        "network_mode": settings.environment_docker_network,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "user": "1000:1000",
        "tmpfs": {"/tmp": "rw,size=256m"},
        "environment": {"HOME": "/tmp"},
        "mem_limit": settings.environment_docker_mem_limit,
        "pids_limit": 512,
        "labels": {"auoreo.session": session_id, "auoreo.environment": "docker"},
    }


def demux(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split Docker's multiplexed stream (`[stream, 0, 0, 0, size:u32be] payload`)."""
    frames: list[bytes] = []
    while len(buf) >= 8:
        size = struct.unpack(">I", buf[4:8])[0]
        if len(buf) < 8 + size:
            break
        frames.append(buf[8 : 8 + size])
        buf = buf[8 + size :]
    return frames, buf


def inside(workdir: str | None) -> str:
    if not workdir:
        return WORKDIR
    return posixpath.normpath(workdir if workdir.startswith("/") else posixpath.join(WORKDIR, workdir))


class Backend(EnvironmentBackend):
    name = "docker"
    remote = False
    client_factory: Callable[..., Any] = staticmethod(_from_env)

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._client: Any = None
        self._containers: dict[str, Any] = {}
        self._roots: dict[str, Path] = {}
        self._lock = asyncio.Lock()
        self.table = ProcTable(self.name, int(getattr(settings, "shell_max_processes", 8) or 8))

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = type(self).client_factory(timeout=60)
        return self._client

    def prepare_session(self, session_id: str, root: Path) -> None:
        self._roots.setdefault(session_id, root)  # the mount is fixed at container creation

    async def check(self) -> BackendStatus:
        def ping() -> Any:
            return type(self).client_factory(timeout=PING_TIMEOUT_S).ping()

        try:
            await asyncio.wait_for(asyncio.to_thread(ping), PING_TIMEOUT_S + 0.5)
        except ImportError:
            return BackendStatus(False, "docker SDK not installed", REMEDY)
        except TimeoutError:
            return BackendStatus(False, f"docker daemon did not answer within {PING_TIMEOUT_S:.0f}s", REMEDY)
        except Exception as exc:
            detail = f"docker daemon unreachable ({type(exc).__name__}: {exc})"[:300]
            return BackendStatus(False, detail, REMEDY)
        st = self.settings
        return BackendStatus(
            True,
            f"docker daemon reachable (image {st.environment_docker_image}, "
            f"network {st.environment_docker_network})",
        )

    async def _container(self, session_id: str) -> Any:
        async with self._lock:
            if session_id not in self._containers:
                root = self._roots.get(session_id, Path.cwd())
                kwargs = container_kwargs(self.settings, session_id, root)
                assert "privileged" not in kwargs  # never
                client = self._get_client()
                self._containers[session_id] = await asyncio.to_thread(client.containers.run, **kwargs)
            return self._containers[session_id]

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        proc = self.table.new(session_id, req.cmd, tty=req.tty)
        try:
            container = await self._container(session_id)
            api = self._get_client().api
            env = {k: v for k, v in req.env.items() if k not in _HOST_ONLY_ENV}
            created = await asyncio.to_thread(
                api.exec_create, container.id, ["bash", "-lc", req.cmd], stdin=True, tty=req.tty,
                workdir=inside(req.workdir), environment=env, user="1000:1000",
            )  # fmt: skip
            exec_id = created["Id"]
            sock = await asyncio.to_thread(api.exec_start, exec_id, socket=True, tty=req.tty)
        except BaseException:
            self.table.procs.pop(proc.id, None)
            raise
        raw = getattr(sock, "_sock", sock)

        async def write(chars: str) -> None:
            await asyncio.to_thread(raw.sendall, chars.encode())

        async def kill() -> None:
            with contextlib.suppress(OSError):
                raw.close()

        proc.writer, proc.killer = write, kill
        proc.spawn(self._pump(proc, raw, exec_id, demuxed=not req.tty))
        return await self.table.collect(proc, req.yield_time_ms)

    async def _pump(self, proc: StreamProc, raw: Any, exec_id: str, *, demuxed: bool) -> None:
        pending = b""
        while True:
            try:
                data = await asyncio.to_thread(raw.recv, 65536)
            except OSError:
                data = b""
            if not data:
                break
            if demuxed:
                frames, pending = demux(pending + data)
                for f in frames:
                    proc.feed(f)
            else:
                proc.feed(data)
        info = await asyncio.to_thread(self._get_client().api.exec_inspect, exec_id)
        code = info.get("ExitCode")
        proc.finish(int(code) if code is not None else -1)

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        return await self.table.write(process_id, chars, yield_time_ms)

    async def close_session(self, session_id: str) -> None:
        await self.table.close_session(session_id)
        self._roots.pop(session_id, None)
        container = self._containers.pop(session_id, None)
        if container is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.remove, force=True)

    async def close(self) -> None:
        for sid in list(self._containers):
            await self.close_session(sid)
        await self.table.close_all()
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None
