"""I2 surfaces: `/api/secrets` write/rotate (names only, audit, hot reload), CLI rotate audit,
doctor pre-demote probe, tool schema shaper, partial tool-call events and empty-reply classes
through the runner, fake structured output, and the v6 usage ledger columns."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import BaseModel
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli
from auoreo.core.diagnostics_i2 import i2_checks, probe, provider_for
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.shaper import shape_for_model, strict_compatible
from auoreo.gateway.types import LLMRequest, TextDelta, Usage
from auoreo.runtime.empty import EMPTY_SENTINEL, classify
from tests.unit.test_loop_recovery import final, run, turns

SECRET = "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123"


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


class _Reloading(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reloads = 0

    def reload_credentials(self) -> None:
        self.reloads += 1


# ------------------------------------------------------------------ secrets API
async def test_secrets_put_rotate_list_are_names_only_audited_and_hot_reload(
    services: Services, client: httpx.AsyncClient
) -> None:
    gw = _Reloading()
    services.gateway = gw
    r = await client.put("/api/secrets/OPENROUTER_API_KEY", json={"value": SECRET})
    assert r.status_code == 200 and r.json()["action"] == "set" and r.json()["reloaded"] is True
    assert SECRET not in r.text
    r = await client.post(
        "/api/secrets/OPENROUTER_API_KEY/rotate", json={"value": SECRET + "X", "grace_s": 60}
    )
    body = r.json()
    assert r.status_code == 200 and body["kept_previous"] is True and body["prev_expires_at"]
    assert SECRET not in r.text and gw.reloads == 2
    assert services.secrets.candidates("OPENROUTER_API_KEY") == [SECRET + "X", SECRET]
    r = await client.post("/api/secrets/provider/openrouter/work/rotate", json={"value": "slot-v"})
    assert r.status_code == 200 and r.json()["name"] == "provider/openrouter/work"
    listed = (await client.get("/api/secrets")).json()["names"]
    assert "OPENROUTER_API_KEY#prev" in listed and not any(n.endswith("#prev_expires") for n in listed)
    for bad in ("AUOREO_SERVER_PASSWORD", "x%23prev"):
        assert (await client.put(f"/api/secrets/{bad}", json={"value": "v"})).status_code == 422
    async with services.db.session() as s:
        from sqlalchemy import select

        from auoreo.core.db import AuditRow

        rows = (await s.execute(select(AuditRow).where(AuditRow.action.like("secrets.%")))).scalars().all()
    actions = [(r.action, r.detail.get("name")) for r in rows]
    assert ("secrets.write", "OPENROUTER_API_KEY") in actions and (
        "secrets.rotate",
        "OPENROUTER_API_KEY",
    ) in actions
    assert SECRET not in json.dumps([r.detail for r in rows])


async def test_secrets_api_requires_auth(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    st = Settings(home=home, gateway="fake", server_password="pw")
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False, with_extensions=False)
    try:
        app = create_app(st, services=svc)
        transport = httpx.ASGITransport(app=app)
        async with (
            LifespanManager(app),
            httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c,
        ):
            assert (await c.put("/api/secrets/OPENAI_API_KEY", json={"value": "v"})).status_code == 401
            ok = await c.get("/api/secrets", headers={"Authorization": "Bearer pw"})
            assert ok.status_code == 200
    finally:
        await svc.close()


def test_cli_rotate_writes_audit_row(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUOREO_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    SecretStore(home).set("OPENAI_API_KEY", SECRET)
    r = CliRunner().invoke(cli, ["secrets", "rotate", "OPENAI_API_KEY", "--generate", "--grace", "1h"])
    assert r.exit_code == 0, r.output
    con = sqlite3.connect(home / "state.db")
    try:
        rows = con.execute("SELECT detail FROM audit WHERE action = 'secrets.rotate'").fetchall()
    finally:
        con.close()
    detail = json.loads(rows[0][0]) if isinstance(rows[0][0], str) else rows[0][0]
    assert detail["name"] == "OPENAI_API_KEY" and detail["kept_previous"] is True


# ------------------------------------------------------------------ doctor probe
async def test_doctor_probes_rotated_key_and_skips_honestly(
    home: Path, httpx_mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretStore(home)
    store.set("OPENROUTER_API_KEY", "old")
    store.rotate("OPENROUTER_API_KEY", "new", grace_s=3600)
    st = Settings(home=home)
    rows = await i2_checks(st)  # AUOREO_OFFLINE=1 in the suite
    assert rows[0].check == "rotation_probe:OPENROUTER_API_KEY" and "skipped" in rows[0].detail
    monkeypatch.delenv("AUOREO_OFFLINE")
    httpx_mock.add_response(url="https://openrouter.ai/api/v1/key", status_code=401)
    bad = await i2_checks(st)
    assert bad[0].level == "error" and "#prev" in bad[0].detail
    assert httpx_mock.get_requests()[0].headers["authorization"] == "Bearer new"
    async with httpx.AsyncClient() as c:
        httpx_mock.add_exception(httpx.ConnectError("offline"))
        off = await probe("OPENROUTER_API_KEY", "new", c)
        assert off.level == "ok" and off.detail.startswith("skipped")
        unknown = await probe("ACME_API_KEY", "v", c)
        assert "no probe" in unknown.detail
    assert provider_for("provider/anthropic/work") == "anthropic"


# ------------------------------------------------------------------ shaper
def test_shaper_flattens_clamps_and_strips_per_family() -> None:
    params = {
        "type": "object",
        "properties": {
            "mode": {"anyOf": [{"type": "string", "enum": [f"v{i}" for i in range(100)]}, {"type": "null"}]},
            "path": {"type": "string", "format": "uri", "default": "/"},
        },
        "required": ["mode"],
    }
    tool = {
        "type": "function",
        "function": {"name": "t", "description": "d", "parameters": params, "strict": True},
    }
    local = shape_for_model("ollama/llama3.1:8b", [tool])[0]["function"]
    mode = local["parameters"]["properties"]["mode"]
    assert "strict" not in local and "anyOf" not in mode and mode["nullable"] is True
    assert len(mode["enum"]) == 64 and "first 64 of 100" in mode["description"]
    assert "format" not in local["parameters"]["properties"]["path"]
    openai = shape_for_model("openai/gpt-4o", [tool])[0]["function"]
    assert "strict" not in openai  # not strict-compatible (optional `path`, no additionalProperties)
    claude = shape_for_model("anthropic/claude-opus-5-5", [tool])[0]["function"]
    assert "strict" not in claude and "anyOf" in claude["parameters"]["properties"]["mode"]
    assert strict_compatible(
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    )
    assert tool["function"]["strict"] is True and len(params["properties"]["mode"]["anyOf"][0]["enum"]) == 100


# ------------------------------------------------------------------ partial events through the runner
async def test_partial_tool_events_stream_then_commit(services: Services, gateway: FakeGateway) -> None:
    gateway.stream_partials = True
    gateway.queue(Scripted(tool_calls=[("echo_nope", {"text": "hello world"})]), Scripted(text="done"))
    status, events, _ = await run(services, "go")
    partials = [e for e in events if e.type == "tool.partial"]
    assert partials and partials[0].data["seq"] == 1 and partials[0].data["call_id"] == "call_1"
    assert "".join(e.data["arg_delta"] for e in partials) == json.dumps({"text": "hello world"})
    assert not [e for e in events if e.type == "tool.partial_drop"]


async def test_partial_drop_when_the_stream_fails(services: Services, gateway: FakeGateway) -> None:
    gateway.stream_partials = True
    gateway.queue(
        Scripted(tool_calls=[("x", {"a": 1})], error=GatewayError("SERVER", "cut")), Scripted(text="ok")
    )
    status, events, _ = await run(services, "go")
    drops = [e for e in events if e.type == "tool.partial_drop"]
    assert status == "completed" and drops and drops[0].data["call_ids"] == ["call_1"]


# ------------------------------------------------------------------ empty classes
def test_empty_classes() -> None:
    from auoreo.gateway.types import ToolCall

    assert classify("", [], "", "stop") == "truly_empty"
    assert classify("  \n", [], "", "stop") == "whitespace"
    assert classify("", [], "hmm", "stop") == "thinking_only"
    assert classify("", [ToolCall("c", "", "{")], "", "tool_calls") == "tool_call_invalid"
    assert classify("", [], "", "content_filter") == "refusal"
    assert classify("", [], "", "length") is None and classify("hi", [], "", "stop") is None


async def test_thinking_only_gets_two_prefill_retries(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(reasoning="thinking"), Scripted(reasoning="more"), Scripted(text="answer"))
    status, events, _ = await run(services, "hi")
    assert status == "completed" and final(events).data["text"] == "answer"
    empties = [e.data for e in events if e.type == "run.empty_response"]
    assert [(d["class"], d["attempt"]) for d in empties] == [("thinking_only", 1), ("thinking_only", 2)]
    assert "Do not think again" in turns(gateway)[1].messages[-1]["content"]


async def test_refusal_is_not_retried_and_persists_empty_sentinel(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(finish="content_filter"))
    status, events, sid = await run(services, "hi")
    assert status == "failed" and final(events).data["error"]["code"] == "EMPTY_RESPONSE"
    assert len(turns(gateway)) == 1
    done = [e for e in events if e.type == "message.completed"]
    assert done[-1].data["content"] == EMPTY_SENTINEL


# ------------------------------------------------------------------ fake structured + ledger v6
class Answer(BaseModel):
    city: str
    population: int


async def test_fake_gateway_honours_response_format() -> None:
    gw = FakeGateway()
    req = LLMRequest(model="m", messages=({"role": "user", "content": "q"},), response_format=Answer)
    text = "".join([i.text async for i in gw.stream(req) if isinstance(i, TextDelta)])
    assert json.loads(text) == {"city": "", "population": 0}
    gw.structured = False
    with pytest.raises(GatewayError) as info:
        _ = [i async for i in gw.stream(req)]
    assert info.value.code == "FORMAT"


async def test_ledger_records_cache_write_reasoning_generation(services: Services) -> None:
    from auoreo.core.usage import UsageLedger
    from auoreo.gateway.usage_parse import parse_usage

    u = parse_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cache_creation_input_tokens": 70,
            "cost": "0.002",
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
    )
    assert u is not None and (u.cache_write_tokens, u.reasoning_tokens, u.cost_usd) == (70, 20, 0.002)
    full = Usage(
        **{**u.__dict__, "generation_id": "gen-1", "deployment": "openrouter/a/b", "tags": ("x", "y")}
    )
    row = await UsageLedger(services.db).record("s1", "r1", "default", full)
    assert (row.cache_write_tokens, row.reasoning_tokens, row.generation_id, row.deployment, row.tags) == (
        70,
        20,
        "gen-1",
        "openrouter/a/b",
        "x,y",
    )
    assert full.event_data()["cache_write_tokens"] == 70
