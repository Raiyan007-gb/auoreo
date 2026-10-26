"""I2 gateway: rotation grace fallback, key pools, budgets, structured output, OpenRouter lane,
1M aliases, per-deployment Router knobs, context tiers and streamed tool-call fragments."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from litellm import exceptions as lx
from pydantic import BaseModel

from auoreo.core import metrics_text
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import ModelGroup, Settings
from auoreo.core.settings_i2 import I2Settings
from auoreo.gateway import aux
from auoreo.gateway.budgets import METRIC
from auoreo.gateway.deploy import ANTHROPIC_1M_BETA, build_model_list
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.gateway import LiteLLMGateway
from auoreo.gateway.resolve import expand_alias
from auoreo.gateway.types import LLMRequest, TextDelta, ToolCall, ToolCallDelta, ToolCallDrop, Usage


def _chunk(text: str = "", usage: Any = None, finish: str | None = None, **kw: Any) -> Any:
    delta = SimpleNamespace(content=text, tool_calls=kw.pop("tool_calls", None))
    choices = (
        [SimpleNamespace(delta=delta, finish_reason=finish)] if text or finish or delta.tool_calls else []
    )
    return SimpleNamespace(choices=choices, usage=usage, **kw)


def _tc(index: int, cid: str | None, name: str | None, args: str) -> Any:
    return SimpleNamespace(index=index, id=cid, function=SimpleNamespace(name=name, arguments=args))


async def _aiter(*chunks: Any, fail: Exception | None = None) -> AsyncIterator[Any]:
    for c in chunks:
        yield c
    if fail is not None:
        raise fail


def _req(model: str = "default", **kw: Any) -> LLMRequest:
    meta = {"session_id": "s1", "run_id": "r1", **kw.pop("metadata", {})}
    return LLMRequest(model=model, messages=({"role": "user", "content": "hi"},), metadata=meta, **kw)


def _gw(home: Any, models: dict[str, list[str]], secret: Any = None, **i2: Any) -> LiteLLMGateway:
    st = Settings(
        home=home,
        models={g: ModelGroup(models=m) for g, m in models.items()},
        fallbacks={},
        i2=I2Settings(**i2),
    )
    gw = LiteLLMGateway(st, secret or (lambda n: "sk-env" if n.endswith("_API_KEY") else None))
    gw.variants = []
    return gw


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_s: float) -> None:
        return None

    monkeypatch.setattr("auoreo.gateway.gateway._backoff_sleep", instant)
    aux.clear_failures()


def _ok_router(gw: LiteLLMGateway, seen: list[dict[str, Any]], fail: Any = None) -> None:
    async def acompletion(**kw: Any) -> Any:
        seen.append(dict(kw))
        if fail is not None and (exc := fail(kw)) is not None:
            raise exc
        return _aiter(_chunk("ok"), _chunk(finish="stop"))

    gw._router = SimpleNamespace(acompletion=acompletion)
    gw._make_router = lambda: SimpleNamespace(acompletion=acompletion)  # type: ignore[method-assign]


# ------------------------------------------------------------------ 1. rotation grace
async def test_auth_error_falls_back_to_prev_key_once_and_audits(home: Any) -> None:
    store = SecretStore(home)
    store.set("ANTHROPIC_API_KEY", "sk-old")
    store.rotate("ANTHROPIC_API_KEY", "sk-new-bad", grace_s=3600)
    gw = _gw(home, {"default": ["anthropic/claude-opus-5-5"]}, secret=store.get)
    audits: list[tuple[str, dict[str, Any]]] = []
    gw.auth.attach(store, lambda a, d: audits.append((a, d)))
    seen: list[dict[str, Any]] = []

    def fail(kw: dict[str, Any]) -> Exception | None:
        key = kw.get("api_key") or gw._model_list[0]["litellm_params"]["api_key"]
        return None if key == "sk-old" else lx.AuthenticationError("bad key", "anthropic", "m")

    _ok_router(gw, seen, fail)
    items = [i async for i in gw.stream(_req())]
    assert any(isinstance(i, TextDelta) for i in items)
    assert [k.get("api_key") for k in seen] == [None, "sk-old"]
    assert audits == [("secret.rotated_fallback", {"name": "ANTHROPIC_API_KEY", "model": "default"})]
    assert "sk-old" not in str(audits)  # names only


async def test_cli_style_write_hot_reloads_router_by_mtime(home: Any) -> None:
    store = SecretStore(home)
    store.set("OPENAI_API_KEY", "sk-1")
    gw = _gw(home, {"default": ["openai/gpt-4o-mini"]}, secret=store.get)
    gw.auth.attach(store)
    assert gw._model_list[0]["litellm_params"]["api_key"] == "sk-1"
    built: list[int] = []
    real = gw._make_router
    gw._make_router = lambda: (built.append(1), real())[1]  # type: ignore[method-assign]
    await asyncio.sleep(0.01)
    store.set("OPENAI_API_KEY", "sk-2")  # another process (CLI) writes secrets.db
    os.utime(store.path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    assert gw.auth.maybe_reload() is True
    assert gw._model_list[0]["litellm_params"]["api_key"] == "sk-2" and built
    assert gw.auth.maybe_reload() is False  # nothing new


# ------------------------------------------------------------------ 2. key pool
async def test_key_pool_round_robin_and_rotate_on_401(home: Any) -> None:
    store = SecretStore(home)
    for slot, val in (("a", "k-a"), ("b", "k-b"), ("c", "k-c")):
        store.set(f"provider/openrouter/{slot}", val)
    gw = _gw(home, {"default": ["openrouter/x/y"]}, secret=store.get, keypool={"cooldown_s": 60})
    audits: list[tuple[str, dict[str, Any]]] = []
    gw.auth.attach(store, lambda a, d: audits.append((a, d)))
    assert gw.has_credentials("openrouter/x/y")
    seen: list[dict[str, Any]] = []
    _ok_router(
        gw,
        seen,
        lambda kw: lx.AuthenticationError("no", "openrouter", "m") if kw["api_key"] == "k-a" else None,
    )
    _ = [i async for i in gw.stream(_req())]
    assert [k["api_key"] for k in seen] == ["k-a", "k-b"]  # rotated to the next slot
    assert audits[0][0] == "gateway.key_rotated" and audits[0][1]["from"] == "provider/openrouter/a"
    seen.clear()
    _ = [i async for i in gw.stream(_req())]
    assert seen[0]["api_key"] in {"k-b", "k-c"}  # `a` is cooling down
    assert "k-" not in str(audits)


async def test_key_pool_rate_limit_rotates_and_refresh_hook_runs_once(home: Any) -> None:
    from auoreo.gateway.keypool import KeyPool

    vals = {"provider/anthropic/x": "old", "provider/anthropic/y": "y"}
    pool = KeyPool("anthropic", lambda: list(vals), vals.get, cooldown_s=30)
    pool.refresh_hooks["provider/anthropic/x"] = lambda _n: "fresh"
    assert pool.select() == ("provider/anthropic/x", "old")
    assert pool.refresh_once("provider/anthropic/x") == "fresh"
    assert pool.refresh_once("provider/anthropic/x") is None  # capped once
    assert pool.rotate("provider/anthropic/x", 429) == ("provider/anthropic/y", "y")
    assert pool.peek() == "provider/anthropic/y" and pool.select() == ("provider/anthropic/y", "y")
    lrf = KeyPool("anthropic", lambda: list(vals), vals.get, strategy="least_recently_failed")
    lrf.fail("provider/anthropic/x")
    lrf.cooldown_s = 0
    assert lrf.select() is not None


# ------------------------------------------------------------------ 3. budgets
async def test_budget_skips_exhausted_deployment_then_fails_budget(home: Any) -> None:
    metrics_text.reset()
    gw = _gw(
        home,
        {"default": ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5"]},
        budgets={"per_key_daily": {"openai/gpt-4o-mini": 1.0}, "provider_daily": {"anthropic": 2.0}},
    )
    seen: list[dict[str, Any]] = []
    _ok_router(gw, seen)
    gw.budgets.add("openai/gpt-4o-mini", 1.5)
    _ = [i async for i in gw.stream(_req())]
    assert seen[-1]["model"] == "anthropic/claude-haiku-4-5"  # pre-pick skipped the spent one
    assert metrics_text.value(METRIC, provider="anthropic") == pytest.approx(2.0, abs=0.01)
    gw.budgets.add("anthropic/claude-haiku-4-5", 2.0)
    assert metrics_text.value(METRIC, provider="anthropic") == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(GatewayError) as info:
        _ = [i async for i in gw.stream(_req())]
    assert info.value.code == "BUDGET" and not info.value.retryable and info.value.status == 429


async def test_budget_spend_recorded_from_usage_and_seeded_from_ledger(home: Any, services: Any) -> None:
    from auoreo.core.usage import UsageLedger
    from auoreo.gateway.budgets import BudgetTracker, seed_from_ledger

    gw = _gw(home, {"default": ["openrouter/a/b"]}, budgets={"tag_daily": {"nightly": 0.5}})

    async def acompletion(**kw: Any) -> Any:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cost=0.25)
        return _aiter(_chunk("ok", id="gen-123", model="a/b"), _chunk(finish="stop"), _chunk(usage=usage))

    gw._router = SimpleNamespace(acompletion=acompletion)
    items = [i async for i in gw.stream(_req(metadata={"tags": ["nightly"]}))]
    u = next(i for i in items if isinstance(i, Usage))
    assert (u.cost_usd, u.generation_id, u.deployment, u.tags) == (
        0.25,
        "gen-123",
        "openrouter/a/b",
        ("nightly",),
    )
    assert gw.budgets.spent("tag", "nightly") == 0.25
    await UsageLedger(services.db).record("s1", "r1", "default", u)
    fresh = BudgetTracker(gw.settings.i2.budgets)
    assert await seed_from_ledger(fresh, services.db) == 1
    assert fresh.spent("provider", "openrouter") == 0.25 and fresh.spent("tag", "nightly") == 0.25


# ------------------------------------------------------------------ 4. structured output
class Answer(BaseModel):
    city: str
    population: int


async def test_response_format_passthrough_and_format_fail_fast(home: Any, monkeypatch: Any) -> None:
    gw = _gw(home, {"default": ["openai/gpt-4o-mini"], "local": ["ollama/llama3.1:8b"]})
    seen: list[dict[str, Any]] = []
    _ok_router(gw, seen)
    _ = [i async for i in gw.stream(_req(response_format=Answer))]
    fmt = seen[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["required"] == ["city", "population"]
    monkeypatch.setattr(gw, "supports_response_schema", lambda _m: False)
    with pytest.raises(GatewayError) as info:
        _ = [i async for i in gw.stream(_req("local", response_format=Answer))]
    assert info.value.code == "FORMAT" and "no silent downgrade" in info.value.message
    gw.settings.i2.fallback_structured = True
    _ = [i async for i in gw.stream(_req("local", response_format=Answer))]
    assert seen[-1]["response_format"] == {"type": "json_object"}
    assert "schema" in seen[-1]["messages"][-1]["content"]


def test_supports_response_schema_uses_litellm_capability(home: Any) -> None:
    gw = _gw(home, {"default": ["openai/gpt-4o-mini"]})
    assert gw.supports_response_schema("openai/gpt-4o-mini") is True
    assert gw.supports_response_schema("nobody/knows") is False


# ------------------------------------------------------------------ 5/6. shaping + partials
async def test_tools_are_shaped_per_family_in_the_pipeline(home: Any) -> None:
    gw = _gw(home, {"default": ["openai/gpt-4o-mini"], "g": ["gemini/gemini-2.5-flash"]})
    seen: list[dict[str, Any]] = []
    _ok_router(gw, seen)
    params = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
        "additionalProperties": False,
    }
    tool = {"type": "function", "function": {"name": "t", "description": "d", "parameters": params}}
    _ = [i async for i in gw.stream(_req(tools=(tool,)))]
    assert seen[0]["tools"][0]["function"]["strict"] is True
    _ = [i async for i in gw.stream(_req("g", tools=(tool,)))]
    sent = seen[1]["tools"][0]["function"]
    assert "strict" not in sent and "additionalProperties" not in sent["parameters"]
    assert "strict" not in tool["function"] and params["additionalProperties"] is False  # never mutated


async def test_streamed_tool_fragments_and_drop_on_failure(home: Any) -> None:
    gw = _gw(home, {"default": ["openai/gpt-4o-mini"]})

    async def ok(**kw: Any) -> Any:
        return _aiter(
            _chunk(tool_calls=[_tc(0, "c1", "read", '{"pa')]),
            _chunk(tool_calls=[_tc(0, None, None, 'th": "x"}')]),
            _chunk(finish="tool_calls"),
        )

    gw._router = SimpleNamespace(acompletion=ok)
    items = [i async for i in gw.stream(_req())]
    parts = [i for i in items if isinstance(i, ToolCallDelta)]
    assert [(p.call_id, p.arg_delta, p.seq) for p in parts] == [("c1", '{"pa', 1), ("c1", 'th": "x"}', 2)]
    assert next(i for i in items if isinstance(i, ToolCall)).arguments == '{"path": "x"}'

    async def broken(**kw: Any) -> Any:
        return _aiter(
            _chunk(tool_calls=[_tc(0, "c9", "read", "{")]), fail=lx.APIConnectionError("cut", "openai", "m")
        )

    gw._router = SimpleNamespace(acompletion=broken)
    got: list[Any] = []
    with pytest.raises(GatewayError):
        async for i in gw.stream(_req()):
            got.append(i)
    assert got[-1] == ToolCallDrop(("c9",))


# ------------------------------------------------------------------ 7. OpenRouter lane
async def test_openrouter_provider_prefs_zdr_and_nitro_gate(home: Any) -> None:
    gw = _gw(
        home,
        {"default": ["openrouter/anthropic/claude-sonnet-4.5:nitro"]},
        openrouter={"sort": "price", "privacy": "zdr"},
    )
    entry = gw._model_list[0]
    assert entry["litellm_params"]["model"] == "openrouter/anthropic/claude-sonnet-4.5"  # :nitro gated
    assert entry["litellm_params"]["extra_body"]["provider"]["zdr"] is True
    seen: list[dict[str, Any]] = []
    _ok_router(gw, seen)
    tool = {"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}
    _ = [i async for i in gw.stream(_req(tools=(tool,)))]
    prov = seen[0]["extra_body"]["provider"]
    assert prov == {
        "sort": {"by": "price", "partition": "none"},
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    assert "order" not in prov and seen[0]["extra_body"]["usage"] == {"include": True}
    allow = _gw(home, {"default": ["openrouter/a/b:nitro"]}, openrouter={"allow_nitro": True})
    assert allow._model_list[0]["litellm_params"]["model"] == "openrouter/a/b:nitro"


async def test_openrouter_catalog_is_cached_offline_safe_and_sorts_aux(home: Any, httpx_mock: Any) -> None:
    from auoreo.gateway import openrouter

    openrouter.clear_catalog()
    httpx_mock.add_response(
        url=openrouter.CATALOG_URL,
        json={
            "data": [
                {"id": "old/fast", "created": 100},
                {"id": "new/fast", "created": 300},
                {"id": "new/fast:free", "created": 400},
                {"id": "x/thinking-model", "created": 500},
            ]
        },
    )
    models = await openrouter.fetch_catalog(3600)
    assert openrouter.recency_sort(models) == ["new/fast", "old/fast"]
    assert await openrouter.fetch_catalog(3600) == models  # cached: no second request
    st = Settings(
        home=home,
        auxiliary={"text_chain": ["openrouter/old/fast", "openrouter/new/fast"]},
        i2=I2Settings(openrouter={"prefer_fast_model": True}),
    )
    assert aux.resolve_aux_model(st, "title") == "openrouter/new/fast"
    openrouter.clear_catalog()
    httpx_mock.add_exception(OSError("offline"), url=openrouter.CATALOG_URL)
    assert await openrouter.fetch_catalog(3600) == []  # offline-safe
    openrouter.clear_catalog()


# ------------------------------------------------------------------ 8. aliases
def test_best_alias_and_1m_deployment_with_beta_header(home: Any) -> None:
    st = Settings(home=home, models={"default": ModelGroup(models=["anthropic/claude-sonnet-5-5"])})
    assert expand_alias(st, "best") == "anthropic/claude-opus-5-5"
    one_m = expand_alias(st, "opus[1m]")
    gw = LiteLLMGateway(st, lambda _n: "k")
    gw._ensure(one_m)
    entry = next(e for e in gw._model_list if e["model_name"] == one_m)
    assert entry["litellm_params"]["model"] == "anthropic/claude-opus-5-5"  # no literal [1m] id
    assert entry["litellm_params"]["extra_headers"]["anthropic-beta"] == ANTHROPIC_1M_BETA
    assert entry["model_info"]["max_input_tokens"] == 1_000_000
    assert gw.context_window(one_m) == 1_000_000
    routed = Settings(home=home)  # OpenRouter default: marker stripped, no Anthropic header
    ml = build_model_list(
        routed.model_copy(
            update={"models": {"big": ModelGroup(models=[expand_alias(routed, "sonnet[1m]")])}}
        ),
        lambda _m: {},
    )
    assert ml[0]["litellm_params"]["model"] == "openrouter/anthropic/claude-sonnet-5.5"
    assert "anthropic-beta" not in ml[0]["litellm_params"].get("extra_headers", {})


# ------------------------------------------------------------------ 9. Router knobs
def test_per_deployment_knobs_and_group_strategy(home: Any, monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    real = litellm.Router

    def spy(**kw: Any) -> Any:
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(litellm, "Router", spy)
    gw = _gw(
        home,
        {"default": ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5"], "small": ["openai/gpt-4o-mini"]},
        deployments={
            "anthropic/claude-haiku-4-5": {
                "max_parallel_requests": 4,
                "base_model": "anthropic/claude-haiku-4-5",
                "allowed_fails": 1,
                "cooldown": 0,
                "order": 2,
                "max_output_tokens": 8192,
            }
        },
        group_strategy={"default": "least-busy"},
    )
    haiku = gw._model_list[1]
    assert haiku["litellm_params"]["max_parallel_requests"] == 4 and haiku["litellm_params"]["order"] == 2
    assert haiku["model_info"] == {
        "max_output_tokens": 8192,
        "base_model": "anthropic/claude-haiku-4-5",
        "allowed_fails": 1,
        "cooldown_time": 0,
    }
    assert "allowed_fails" not in haiku["litellm_params"]  # router-internal, never sent upstream
    assert captured["routing_groups"] == [
        {"group_name": "auoreo-default", "models": ["default"], "routing_strategy": "least-busy"}
    ]
    assert captured["routing_strategy"] == "simple-shuffle"  # defaults unchanged elsewhere


# ------------------------------------------------------------------ 11. context tiers
async def test_context_window_tiers_and_min_context_guard(home: Any) -> None:
    gw = _gw(
        home,
        {
            "default": ["openai/gpt-4o-mini"],
            "tiny": ["openai/gpt-3.5-turbo"],
            "mystery": ["openai/zzz-claude-x"],
        },
        deployments={"openai/gpt-4o-mini": {"max_input_tokens": 99_000}},
    )
    assert gw._window("default").source == "deployment" and gw.context_window("default") == 99_000
    assert gw._window("mystery").source == "table" and gw.context_window("mystery") == 200_000
    unknown = gw._window("nobody/knows")
    assert (unknown.source, unknown.tokens) == ("probe", 128_000)
    tiny = gw._window("tiny")
    assert tiny.source == "model_info" and tiny.tokens < 64_000
    with pytest.raises(GatewayError) as info:
        _ = [i async for i in gw.stream(_req("tiny"))]
    assert info.value.code == "INVALID_REQUEST" and "min_context" in info.value.message
    seen: list[dict[str, Any]] = []
    _ok_router(gw, seen)
    _ = [i async for i in gw.stream(_req("tiny", metadata={"purpose": "title"}))]  # aux: no guard
    assert seen
