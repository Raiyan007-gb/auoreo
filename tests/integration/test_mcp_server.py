"""`POST /mcp`: AUOREO's tools served to external MCP clients over streamable HTTP
(real uvicorn; the SDK client speaks HTTP)."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import httpx2
import pytest
import uvicorn
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from mcp import Client


@asynccontextmanager
async def running_server(services: Services, settings: Settings) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings, services=services), log_level="warning"))
    serve = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serve


def text_of(result: object) -> str:
    return "\n".join(getattr(b, "text", "") for b in getattr(result, "content", []))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_list_read_and_deny(services: Services, settings: Settings, workdir: Path, mode: str) -> None:
    (workdir / "notes.txt").write_text("alpha\nbeta\n")
    async with running_server(services, settings) as base, Client(f"{base}/mcp", mode=mode) as client:  # type: ignore[arg-type]
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert {"read", "bash"} <= names
        read = next(t for t in tools.tools if t.name == "read")
        assert read.input_schema["type"] == "object" and "path" in read.input_schema["properties"]
        assert "non-interactively" in (read.description or "")

        result = await client.call_tool("read", {"path": str(workdir / "notes.txt")})
        assert not result.is_error
        assert "alpha" in text_of(result) and "beta" in text_of(result)

        denied = await client.call_tool("bash", {"command": "echo hi"})
        assert denied.is_error and "non-interactive" in text_of(denied)

        bad = await client.call_tool("read", {})
        assert bad.is_error
    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow).where(AuditRow.action == "tool.call"))).scalars().all()
    assert any(r.session_id and r.session_id.startswith("mcp-") for r in rows)


async def test_auth_and_origin(services: Services, settings: Settings) -> None:
    settings.server_password = "pw"
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    accept = {"Accept": "application/json, text/event-stream"}
    async with running_server(services, settings) as base:
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            r = await c.post("/mcp", json=init, headers=accept)
            assert r.status_code == 401
            r = await c.post("/mcp", json=init, headers={**accept, "Authorization": "Bearer wrong"})
            assert r.status_code == 401
            evil = {**accept, "Authorization": "Bearer pw", "Origin": "https://evil.example"}
            assert (await c.post("/mcp", json=init, headers=evil)).status_code == 403
            ok = {**accept, "Authorization": "Bearer pw", "MCP-Protocol-Version": "2025-06-18"}
            r = await c.post("/mcp", json=init, headers=ok)
            assert r.status_code == 200
            assert r.headers.get("mcp-session-id")
            assert r.headers.get("mcp-protocol-version") == "2025-06-18"

        headers = {"Authorization": "Bearer pw"}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=10) as http,
            Client(streamable_http_client(f"{base}/mcp", http_client=http)) as client,
        ):
            assert any(t.name == "read" for t in (await client.list_tools()).tools)


async def test_disabled(services: Services, settings: Settings) -> None:
    settings.mcp_server_enabled = False
    async with running_server(services, settings) as base, httpx.AsyncClient(base_url=base) as c:
        assert (await c.post("/mcp", json={})).status_code in {404, 405}
