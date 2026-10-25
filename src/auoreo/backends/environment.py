"""Execution-environment backend kind (`backends/environment/<name>/`, M6).

An environment is *where* commands run: the local machine, a container, an SSH host, or a
cloud sandbox (E2B / Modal / Daytona), or a remote AUOREO exec-server. The shape follows the
spec's execution plane (`exec_command{cmd,workdir,tty,yield_time_ms,max_output_tokens}` +
`write_stdin`, L271). Permission floors are evaluated by the tool before any backend runs;
an environment never widens what the permission engine allowed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class ExecRequest:
    cmd: str
    workdir: str | None = None
    """Path inside the environment; None = its default working directory."""
    tty: bool = False
    yield_time_ms: int = 10_000
    max_output_bytes: int = 64_000
    env: dict[str, str] = field(default_factory=dict)
    """Already scrubbed by the caller (`core.secrets.child_env`)."""


@dataclass
class ExecChunk:
    output: str
    exit_code: int | None = None
    """None while the process is still running (then `process_id` is set)."""
    process_id: str | None = None
    truncated: bool = False


class EnvironmentBackend(ABC):
    name: str = "environment"
    remote: bool = False
    """True when commands leave this machine (audited as `environment.exec{remote: true}`)."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def exec_command(self, req: ExecRequest, *, session_id: str) -> ExecChunk:
        """Start `req.cmd`; return output gathered until exit or `yield_time_ms`."""

    @abstractmethod
    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> ExecChunk:
        """Write to a running process (empty `chars` = poll) and return new output."""

    async def upload(self, local_path: str, remote_path: str) -> None:
        raise NotImplementedError(f"{self.name}: upload is not supported")

    async def download(self, remote_path: str, local_path: str) -> None:
        raise NotImplementedError(f"{self.name}: download is not supported")

    async def close_session(self, session_id: str) -> None:
        """Kill processes and release remote resources owned by `session_id`."""
        return None

    async def close(self) -> None:
        """Release everything (server shutdown)."""
        return None
