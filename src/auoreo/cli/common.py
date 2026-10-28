"""Helpers shared by CLI commands."""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console

from auoreo.core.services import Services
from auoreo.core.settings import Settings

T = TypeVar("T")
console = Console()
err_console = Console(stderr=True)


def settings(**overrides: Any) -> Settings:
    return Settings(**{k: v for k, v in overrides.items() if v is not None})


def with_services[T](
    fn: Callable[[Services], Awaitable[T]],
    st: Settings | None = None,
    *,
    memory: bool = True,
    mcp: bool = False,
) -> T:
    async def go() -> T:
        svc = await Services.create(st or settings(), cwd=Path.cwd(), with_memory=memory, with_mcp=mcp)
        try:
            return await fn(svc)
        finally:
            await svc.close()

    return asyncio.run(go())


def dump_json(obj: Any) -> None:
    from auoreo.cli.output import dumps  # --- I5: `--mini` prints one line

    print(dumps(obj))


def tcp_listener(host: str) -> socket.socket:
    """An unbound TCP socket that will not share its port. POSIX: SO_REUSEADDR (fast restart
    over TIME_WAIT). Windows: SO_EXCLUSIVEADDRUSE, because there SO_REUSEADDR lets a second
    server bind a port that is already listening, and requests go to either one."""
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock
