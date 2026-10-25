"""CLI plumbing for `auoreo cron|webhook|deliveries`: talk to a running server (`--url`)
or work in-process on `state.db` without starting the extensions (no worker, no triggers)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import typer

from auoreo.core.services import Services
from auoreo.core.settings import Settings

URL_HELP = "Base URL of a running server (env AUOREO_URL); omit to work in-process on state.db."


def local[T](fn: Callable[[Services], Awaitable[T]], st: Settings | None = None) -> T:
    async def go() -> T:
        svc = await Services.create(
            st or Settings(), cwd=Path.cwd(), with_memory=False, with_mcp=False, with_extensions=False
        )
        try:
            return await fn(svc)
        finally:
            await svc.close()

    return asyncio.run(go())


def remote(
    method: str,
    url: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    auth: bool = True,
) -> httpx.Response:
    hdrs = dict(headers or {})
    password = Settings().server_password
    if auth and password:
        hdrs["Authorization"] = f"Bearer {password}"
    try:
        return httpx.request(
            method,
            url.rstrip("/") + path,
            json=json,
            params=params,
            content=content,
            headers=hdrs,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        typer.echo(f"cannot reach {url}: {exc}", err=True)
        raise typer.Exit(2) from exc


def check(resp: httpx.Response, ok: tuple[int, ...] = (200, 201, 202)) -> Any:
    if resp.status_code not in ok:
        typer.echo(f"server answered {resp.status_code}: {resp.text}", err=True)
        raise typer.Exit(1)
    return resp.json()
