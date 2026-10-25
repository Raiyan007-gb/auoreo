"""ACP (Agent Client Protocol) JSON-RPC 2.0 at `POST /acp`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=20) as c,
    ):
        yield c


def rpc(method: str, params: dict[str, Any] | None = None, rid: int | None = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        msg["id"] = rid
    return msg


async def call(c: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None) -> Any:
    r = await c.post("/acp", json=rpc(method, params))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    return body


async def test_initialize(client: httpx.AsyncClient) -> None:
    body = await call(client, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
    res = body["result"]
    assert res["protocolVersion"] == 1 and res["authMethods"] == []
    assert res["agentCapabilities"] == {
        "loadSession": True,
        "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
        "sessionCapabilities": {"list": {}, "resume": {}, "close": {}},
    }


async def test_new_load_prompt(client: httpx.AsyncClient, services: Services, workdir: Path) -> None:
    res = (await call(client, "session/new", {"cwd": str(workdir), "mcpServers": []}))["result"]
    sid = res["sessionId"]
    assert "_meta" not in res
    assert await services.get_session(sid) is not None
    assert (await call(client, "session/load", {"sessionId": sid}))["result"] == {}
    prompt = [
        {"type": "text", "text": "hello"},
        {"type": "resource", "resource": {"uri": "file:///tmp/a.txt", "text": "ctx"}},
    ]
    res = (await call(client, "session/prompt", {"sessionId": sid, "prompt": prompt}))["result"]
    assert res["stopReason"] == "end_turn"
    assert res["_meta"]["auoreo"]["text"].startswith("echo: hello")
    assert 'uri="file:///tmp/a.txt"' in res["_meta"]["auoreo"]["text"]


async def test_new_warns_about_mcp_servers_and_cwd(client: httpx.AsyncClient) -> None:
    res = (await call(client, "session/new", {"cwd": "/elsewhere", "mcpServers": [{"name": "x"}]}))["result"]
    warnings = res["_meta"]["auoreo"]["warnings"]
    assert any("mcpServers" in w for w in warnings) and any("/elsewhere" in w for w in warnings)
    body = await call(client, "session/new", {"cwd": "relative/path"})
    assert body["error"]["code"] == -32602


async def test_prompt_errors(client: httpx.AsyncClient) -> None:
    body = await call(
        client, "session/prompt", {"sessionId": "nope", "prompt": [{"type": "text", "text": "x"}]}
    )
    assert body["error"]["code"] == -32602
    sid = (await call(client, "session/new"))["result"]["sessionId"]
    body = await call(
        client, "session/prompt", {"sessionId": sid, "prompt": [{"type": "image", "data": "x"}]}
    )
    assert body["error"]["code"] == -32602
    body = await call(client, "session/load", {"sessionId": "nope"})
    assert body["error"]["code"] == -32602


async def test_cancel_running_prompt(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = (await call(client, "session/new"))["result"]["sessionId"]
    started = asyncio.Event()
    original = gateway.stream

    async def slow(request: Any) -> Any:
        if request.metadata.get("purpose") is None:
            started.set()
            await asyncio.sleep(30)
        async for item in original(request):
            yield item

    monkeypatch.setattr(gateway, "stream", slow)
    prompt = asyncio.create_task(
        client.post(
            "/acp", json=rpc("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "x"}]})
        )
    )
    await asyncio.wait_for(started.wait(), 10)
    r = await client.post("/acp", json=rpc("session/cancel", {"sessionId": sid}, rid=None))
    assert r.status_code == 204 and r.content == b""
    res = (await asyncio.wait_for(prompt, 10)).json()["result"]
    assert res["stopReason"] == "cancelled"
    # idempotent and never an error for unknown ids
    r = await client.post("/acp", json=rpc("session/cancel", {"sessionId": "unknown"}, rid=None))
    assert r.status_code == 204
    body = await call(client, "session/cancel", {"sessionId": "unknown"})
    assert body["result"] is None


async def test_batch_and_notifications(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text="x"))
    r = await client.post(
        "/acp",
        json=[
            rpc("initialize", rid=1),
            rpc("session/new", rid=2),
            rpc("session/cancel", {"sessionId": "z"}, rid=None),
        ],
    )
    assert r.status_code == 200
    out = {m["id"]: m for m in r.json()}
    assert set(out) == {1, 2} and out[1]["result"]["protocolVersion"] == 1
    assert out[2]["result"]["sessionId"].startswith("ses_")
    # only notifications → 204
    r = await client.post(
        "/acp", json=[rpc("session/cancel", {"sessionId": "a"}, rid=None), rpc("_x/ping", rid=None)]
    )
    assert r.status_code == 204
    # too big
    r = await client.post("/acp", json=[rpc("initialize", rid=i) for i in range(33)])
    assert r.json()["error"]["code"] == -32600


async def test_protocol_errors(client: httpx.AsyncClient) -> None:
    r = await client.post("/acp", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["error"]["code"] == -32700 and r.json()["id"] is None
    body = await call(client, "session/set_model", {})
    assert body["error"]["code"] == -32601
    r = await client.post("/acp", json={"id": 5, "method": "initialize"})  # missing jsonrpc
    assert r.json()["error"]["code"] == -32600 and r.json()["id"] == 5
    r = await client.post("/acp", json=[1, rpc("initialize", rid=2)])
    errs = r.json()
    assert errs[0]["error"]["code"] == -32600 and errs[1]["result"]["protocolVersion"] == 1
    r = await client.post("/acp", json=rpc("unknown/notification", rid=None))
    assert r.status_code == 204
    r = await client.post("/acp", json={"jsonrpc": "2.0", "id": 3, "method": "session/new", "params": [1]})
    assert r.json()["error"]["code"] == -32602


async def test_auth_required(services: Services, settings: Settings) -> None:
    settings.server_password = "pw"
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.post("/acp", json=rpc("initialize"))).status_code == 401
        r = await c.post("/acp", json=rpc("initialize"), headers={"Authorization": "Bearer pw"})
        assert r.status_code == 200
