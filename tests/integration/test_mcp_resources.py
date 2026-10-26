"""F3: MCP resources + prompts at `/mcp` (real uvicorn; the SDK client speaks HTTP)."""

from __future__ import annotations

import asyncio
import json
import socket
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from mcp.shared.exceptions import MCPError
from sqlalchemy import select

from auoreo.api import mcp_resources
from auoreo.api.app import create_app
from auoreo.api.mcp_server import McpEndpoint
from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from mcp import Client

SECRET = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@asynccontextmanager
async def serve(services: Services, settings: Settings) -> AsyncIterator[tuple[str, McpEndpoint]]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_app(settings, services=services)
    endpoint = next(m for m in app.state.mounts if isinstance(m, McpEndpoint))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        yield f"http://127.0.0.1:{port}/mcp", endpoint
    finally:
        server.should_exit = True
        await task


def memory(settings: Settings, name: str, text: str) -> Path:
    d = settings.home / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    return d / name


async def audit_rows(services: Services) -> list[AuditRow]:
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == "mcp.resource.read").order_by(AuditRow.seq)
        return list((await s.execute(q)).scalars())


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_list_read_templates_redaction(services: Services, settings: Settings, mode: str) -> None:
    memory(settings, "MEMORY.md", f"# Memory\nkey: {SECRET}\nANTHROPIC_API_KEY={SECRET}\n")
    session = await services.create_session(title="hello")
    async with serve(services, settings) as (url, _), Client(url, mode=mode) as client:  # type: ignore[arg-type]
        caps = client.session.server_capabilities  # type: ignore[union-attr]
        assert caps is not None and caps.resources is not None and caps.prompts is not None

        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert "auoreo://memory/MEMORY.md" in uris
        assert "auoreo://harness/tools" in uris and "auoreo://harness/agents" in uris
        assert f"auoreo://sessions/{session.id}" in uris
        assert any(u.startswith("auoreo://skills/") for u in uris)

        templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}
        assert templates == {t[1] for t in mcp_resources.TEMPLATES}

        mem = await client.read_resource("auoreo://memory/MEMORY.md", cache_mode="bypass")
        text = mem.contents[0].text  # type: ignore[union-attr]
        assert "# Memory" in text and SECRET not in text

        tools = await client.read_resource("auoreo://harness/tools", cache_mode="bypass")
        names = {row["name"] for row in json.loads(tools.contents[0].text)}  # type: ignore[union-attr]
        assert "read" in names

        skill = next(u for u in uris if u.startswith("auoreo://skills/"))
        body = (await client.read_resource(skill, cache_mode="bypass")).contents[0].text  # type: ignore[union-attr]
        assert body.startswith("---")

        sess = await client.read_resource(f"auoreo://sessions/{session.id}", cache_mode="bypass")
        assert session.id in sess.contents[0].text  # type: ignore[union-attr]

        for bad in (
            "auoreo://memory/../secrets.db",
            "auoreo://nope/x",
            "file:///etc/passwd",
            "auoreo://sessions/zz",
        ):
            with pytest.raises(MCPError) as err:
                await client.read_resource(bad, cache_mode="bypass")
            assert err.value.code == -32602
    rows = await audit_rows(services)
    assert len(rows) == 8
    details = [services.audit.plain_detail(r) for r in rows]
    assert details[0]["uri"] == "auoreo://memory/MEMORY.md" and rows[0].decision == "allow"
    assert [r.decision for r in rows[-4:]] == ["deny"] * 4
    assert all(r.session_id and r.session_id.startswith("mcp-") for r in rows)


async def test_size_cap(services: Services, settings: Settings) -> None:
    memory(settings, "USER.md", "x" * (300 * 1024))
    async with serve(services, settings) as (url, _), Client(url) as client:
        res = await client.read_resource("auoreo://memory/USER.md", cache_mode="bypass")
    text = res.contents[0].text  # type: ignore[union-attr]
    assert "truncated by AUOREO" in text and len(text.encode()) < 256 * 1024 + 200
    detail = services.audit.plain_detail((await audit_rows(services))[-1])
    assert detail["truncated"] is True


def test_cap_helper() -> None:
    assert mcp_resources.cap("abc", 10) == ("abc", False)
    text, cut = mcp_resources.cap("é" * 10, 5)  # never splits a code point
    assert cut and text.startswith("éé") and "5 of 20 bytes" in text


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_prompts(services: Services, settings: Settings, mode: str) -> None:
    async with serve(services, settings) as (url, _), Client(url, mode=mode) as client:  # type: ignore[arg-type]
        prompts = {p.name: p for p in (await client.list_prompts()).prompts}
        assert "review" in prompts and "goal" in prompts
        assert not {"compact", "model", "plan"} & set(prompts)
        assert prompts["review"].arguments and prompts["review"].arguments[0].name == "args"

        got = await client.get_prompt("review", {"args": "src/app.py"})
        assert got.messages[0].role == "user"
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert text.startswith("Load the `code-review` skill and review: src/app.py\n")
        assert f"worktree at `{services.cwd}`" in text  # G6: no worktree over MCP → the cwd

        empty = await client.get_prompt("review")
        assert empty.messages[0].content.text.startswith("Load the")  # type: ignore[union-attr]

        for name in ("compact", "missing"):
            with pytest.raises(MCPError):
                await client.get_prompt(name)


async def test_listen_memory_update_and_reload(services: Services, settings: Settings) -> None:
    path = memory(settings, "MEMORY.md", "one\n")
    async with serve(services, settings) as (url, endpoint), Client(url) as client:
        endpoint.catalog.POLL_INTERVAL = 0.05
        async with client.listen(
            resource_subscriptions=["auoreo://memory/MEMORY.md"], resources_list_changed=True
        ) as sub:
            await asyncio.sleep(0.1)
            path.write_text("two\n")
            import os

            os.utime(path, ns=(1, 10**18))
            services.harness.reload()
            seen: list[str] = []
            async with asyncio.timeout(5):
                async for event in sub:
                    seen.append(type(event).__name__ + ":" + str(getattr(event, "uri", "")))
                    if len(seen) >= 2:
                        break
        assert "ResourceUpdated:auoreo://memory/MEMORY.md" in seen
        assert any(s.startswith("ResourcesListChanged") for s in seen)
    assert endpoint.catalog._poller is None or endpoint.catalog._poller.done()


async def test_legacy_subscribe(services: Services, settings: Settings) -> None:
    path = memory(settings, "PREFS.md", "a\n")
    got: list[str] = []

    async def handler(message: object) -> None:
        root = getattr(message, "root", message)
        params = getattr(root, "params", None)
        if getattr(root, "method", "") == "notifications/resources/updated" and params is not None:
            got.append(str(params.uri))

    async with (
        serve(services, settings) as (url, endpoint),
        Client(url, mode="legacy", message_handler=handler) as client,  # type: ignore[arg-type]
    ):
        caps = client.session.server_capabilities  # type: ignore[union-attr]
        assert caps is not None and caps.resources is not None and caps.resources.subscribe
        endpoint.catalog.POLL_INTERVAL = 0.05
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            await client.subscribe_resource("auoreo://memory/PREFS.md")
        await asyncio.sleep(0.1)
        import os

        os.utime(path, ns=(1, 10**18))
        async with asyncio.timeout(5):
            while not got:
                await asyncio.sleep(0.05)
        assert got == ["auoreo://memory/PREFS.md"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            await client.unsubscribe_resource("auoreo://memory/PREFS.md")
        assert not endpoint.catalog.has_subscribers
