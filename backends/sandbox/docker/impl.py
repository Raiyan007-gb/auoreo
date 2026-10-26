"""Docker sandbox: one throwaway container per call (Docker SDK for Python, imported lazily).

Never `privileged`; all capabilities dropped; `no-new-privileges`; uid 1000; pids/memory
limits; `network_mode="none"` unless the tool's `net` is proxy/direct.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, SandboxError

WORKDIR = "/work"
PING_TIMEOUT_S = 3.0
REMEDY = (
    "start the Docker daemon (or set DOCKER_HOST) and install the extra "
    "(`uv sync --extra sandbox-docker`), or select another backend with "
    'settings.backends.sandbox = "none"'
)
# Host-specific variables that would break the container's own PATH/HOME.
_HOST_ONLY_ENV = frozenset({"PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "SHELL", "PWD"})


def _from_env(**kwargs: Any) -> Any:
    import docker

    return docker.from_env(**kwargs)


def run_kwargs(spec: ExecSpec, image: str, mem_limit: str, extra: list[Any]) -> dict[str, Any]:
    """Arguments for `client.containers.run` (pure; unit-tested)."""
    read_only = spec.mode == "read-only"
    volumes: dict[str, dict[str, str]] = {
        str(spec.cwd): {"bind": WORKDIR, "mode": "ro" if read_only else "rw"}
    }
    writable = {str(p.resolve()) for p in spec.writable}
    for p in extra:
        rw = str(p) in writable and not read_only
        volumes[str(p)] = {"bind": str(p), "mode": "rw" if rw else "ro"}
    return {
        "image": image,
        "command": list(spec.argv),
        "working_dir": WORKDIR,
        "volumes": volumes,
        "environment": {k: v for k, v in spec.env.items() if k not in _HOST_ONLY_ENV},
        # I9: `proxy` cannot reach the host-loopback egress proxy from a container → no network
        "network_mode": "bridge" if spec.net == "direct" else "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": read_only,
        "tmpfs": {"/tmp": "rw,size=64m"},
        "mem_limit": mem_limit,
        "pids_limit": 256,
        "user": "1000:1000",
        "detach": True,
        "stdout": True,
        "stderr": True,
    }


class Backend(SandboxBackend):
    name = "docker"
    client_factory: Callable[..., Any] = staticmethod(_from_env)

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = type(self).client_factory(timeout=60)
        return self._client

    async def check(self) -> BackendStatus:
        def ping() -> Any:
            return type(self).client_factory(timeout=PING_TIMEOUT_S).ping()

        try:
            await asyncio.wait_for(asyncio.to_thread(ping), PING_TIMEOUT_S + 0.5)
        except ImportError:
            return BackendStatus(False, "docker SDK not installed", remedy=REMEDY)
        except TimeoutError:
            return BackendStatus(False, f"docker daemon did not answer within {PING_TIMEOUT_S:.0f}s", REMEDY)
        except Exception as exc:  # DockerException: socket missing, permission denied, …
            return BackendStatus(
                False, f"docker daemon unreachable ({type(exc).__name__}: {exc})"[:300], REMEDY
            )
        return BackendStatus(True, f"docker daemon reachable (image {self.settings.sandbox_docker_image})")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    async def run(self, spec: ExecSpec) -> ExecResult:
        if spec.stdin is not None:
            raise SandboxError("the docker backend does not support stdin")
        extra = await self.check_mounts(spec)
        kwargs = run_kwargs(
            spec, self.settings.sandbox_docker_image, self.settings.sandbox_docker_mem_limit, extra
        )
        assert "privileged" not in kwargs  # never, whatever the mode
        return await asyncio.to_thread(self._run_sync, spec, kwargs)

    def _run_sync(self, spec: ExecSpec, kwargs: dict[str, Any]) -> ExecResult:
        client = self._get_client()
        container = client.containers.run(**kwargs)
        started = time.monotonic()
        timed_out = False
        code = -1
        try:
            try:
                status = container.wait(timeout=spec.timeout_s)
                code = int(status.get("StatusCode", -1))
            except Exception:  # requests ReadTimeout / ConnectionError on timeout
                if time.monotonic() - started < spec.timeout_s * 0.9:
                    raise
                timed_out = True
                with contextlib.suppress(Exception):
                    container.kill()
            if timed_out:
                return ExecResult(-9, "", "", True, self.name, True)
            if spec.merge_stderr:
                out = container.logs(stdout=True, stderr=True)
                err = b""
            else:
                out = container.logs(stdout=True, stderr=False)
                err = container.logs(stdout=False, stderr=True)
            return ExecResult(
                code,
                (out or b"").decode("utf-8", errors="replace"),
                (err or b"").decode("utf-8", errors="replace"),
                False,
                self.name,
                True,
            )
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None
