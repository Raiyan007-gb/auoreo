"""Helpers shared by CLI commands."""

from __future__ import annotations

import asyncio
import json
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
    print(json.dumps(obj, indent=2, default=str))
