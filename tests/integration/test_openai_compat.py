"""OpenAI-compatible `/v1` surface: translate → gateway, no server-side tool execution."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow, UsageRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def chunks(text: str) -> list[Any]:
    out: list[Any] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                payload = line[6:]
                out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


def compat_requests(gateway: FakeGateway) -> list[Any]:
    return [r for r in gateway.requests if r.metadata.get("purpose") == "compat"]


async def test_models_lists_groups_and_aliases(client: httpx.AsyncClient, settings: Settings) -> None:
    settings.aliases["fast"] = "small"
    r = await client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    ids = [m["id"] for m in body["data"]]
    assert body["object"] == "list"
    assert {"default", "small", "auoreo/default", "fast"} <= set(ids)
    assert all(m["object"] == "model" and m["owned_by"] == "auoreo" for m in body["data"])


async def test_non_stream_completion_with_shaping_and_ledger(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "auoreo/default", "messages": [{"role": "user", "content": "hello"}], "user": "u1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion" and body["id"].startswith("chatcmpl-")
    choice = body["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "echo: hello", "tool_calls": None}
    assert choice["finish_reason"] == "stop"
    assert (
        body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )
    req = compat_requests(gateway)[-1]
    assert req.model == "default"  # prefix stripped
    assert req.temperature == 0.4  # variants.yaml default
    assert req.metadata["user"] == "u1"
    async with services.db.session() as s:
        usage = (await s.execute(select(UsageRow).where(UsageRow.session_id == "compat"))).scalars().all()
        audit = (await s.execute(select(AuditRow).where(AuditRow.action == "compat.openai"))).scalars().all()
    assert len(usage) == 1 and usage[0].run_id == body["id"]
    assert audit and audit[-1].detail["model"] == "default"
    assert "hello" not in json.dumps(audit[-1].detail)  # never message bodies


async def test_variant_omit_fields(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "openai/o3-mini", "messages": [{"role": "user", "content": "x"}], "temperature": 1},
    )
    assert r.status_code == 200
    assert compat_requests(gateway)[-1].omit_fields == ("temperature",)


async def test_streaming_chunks_usage_and_done(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text="a fairly long streamed answer, chunked by the fake"))
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    parts = chunks(r.text)
    assert parts[-1] == "[DONE]"
    objs = parts[:-1]
    assert all(o["object"] == "chat.completion.chunk" for o in objs)
    assert objs[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(o["choices"][0]["delta"].get("content", "") for o in objs if o["choices"])
    assert text == "a fairly long streamed answer, chunked by the fake"
    finish = [o for o in objs if o["choices"] and o["choices"][0]["finish_reason"]]
    assert finish[0]["choices"][0]["finish_reason"] == "stop"
    usage_chunk = objs[-1]
    assert usage_chunk["choices"] == [] and usage_chunk["usage"]["total_tokens"] > 0
    assert all(o["usage"] is None for o in objs[:-1])


async def test_tool_calls_round_trip(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]
    gateway.queue(Scripted(tool_calls=[("get_weather", {"city": "Oslo"})]))
    messages: list[dict[str, Any]] = [{"role": "user", "content": "weather?"}]
    r = await client.post(
        "/v1/chat/completions", json={"model": "default", "messages": messages, "tools": tools}
    )
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function" and call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Oslo"}
    req = compat_requests(gateway)[-1]
    assert req.tools[0]["function"]["name"] == "get_weather"

    # the client runs the tool and sends the result back
    messages += [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": "sunny"},
    ]
    r = await client.post(
        "/v1/chat/completions", json={"model": "default", "messages": messages, "tools": tools}
    )
    assert r.json()["choices"][0]["message"]["content"] == "Tool result:\nsunny"

    # streamed tool call deltas
    gateway.queue(Scripted(tool_calls=[("get_weather", {"city": "Rome"})]))
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "default",
            "messages": messages[:1],
            "tools": tools,
            "stream": True,
            "tool_choice": "required",
        },
    )
    objs = [o for o in chunks(r.text) if o != "[DONE]"]
    deltas = [o["choices"][0]["delta"] for o in objs if o["choices"]]
    tc = next(d["tool_calls"][0] for d in deltas if "tool_calls" in d)
    assert tc["index"] == 0 and tc["id"] and tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Rome"}
    assert objs[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert "usage" not in objs[-1]  # no include_usage → no usage field


async def test_gateway_error_envelope(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(error=GatewayError("AUTH", "bad provider key")))
    r = await client.post(
        "/v1/chat/completions", json={"model": "default", "messages": [{"role": "user", "content": "x"}]}
    )
    assert r.status_code == 401
    assert r.json() == {
        "error": {"message": "bad provider key", "type": "authentication_error", "code": "auth"}
    }
    gateway.queue(Scripted(error=GatewayError("RATE_LIMIT", "slow down")))
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "default", "messages": [{"role": "user", "content": "x"}], "stream": True},
    )
    assert r.status_code == 429 and r.json()["error"]["type"] == "rate_limit_error"
    gateway.queue(Scripted(error=GatewayError("SERVER", "boom")))
    r = await client.post(
        "/v1/chat/completions", json={"model": "default", "messages": [{"role": "user", "content": "x"}]}
    )
    assert r.status_code == 502 and r.json()["error"]["code"] == "server"


async def test_response_text_is_redacted(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    secret = "sk-proj-" + "A" * 30
    gateway.queue(Scripted(text=f"the key is {secret} ok"))
    r = await client.post(
        "/v1/chat/completions", json={"model": "default", "messages": [{"role": "user", "content": "k"}]}
    )
    assert secret not in r.text
    gateway.queue(Scripted(text=f"the key is {secret} ok"))
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "default", "messages": [{"role": "user", "content": "k"}], "stream": True},
    )
    text = "".join(
        o["choices"][0]["delta"].get("content", "") for o in chunks(r.text) if o != "[DONE]" and o["choices"]
    )
    assert secret not in text and text.endswith(" ok")


async def test_auth_required(services: Services, settings: Settings) -> None:
    settings.server_password = "pw"
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        assert (await c.get("/v1/models")).status_code == 401
        ok = await c.get("/v1/models", headers={"Authorization": "Bearer pw"})
        assert ok.status_code == 200
