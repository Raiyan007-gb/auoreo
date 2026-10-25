"""Run an ASGI app on a real uvicorn server (TCP or Unix socket) inside the test loop."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn

from auoreo.remote.uds import bind_unix


@asynccontextmanager
async def serving(app: Any, *, uds: Path | None = None) -> AsyncIterator[str]:
    """Yields `http://127.0.0.1:<port>` (TCP) or `unix:<path>` (when `uds` is given)."""
    if uds is not None:
        sock = bind_unix(uds)
        where = f"unix:{uds}"
    else:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        where = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.02)
        yield where
    finally:
        server.should_exit = True
        await task
        if uds is not None:
            await asyncio.to_thread(uds.unlink, missing_ok=True)
