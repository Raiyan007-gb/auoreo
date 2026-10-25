"""LiteLLM gateway behaviour for G3: 529 → fallback, auth recovery, error cause, Router
options, per-turn headers, caching on the wire, usage details, stream idle timeout."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from litellm import exceptions as lx

from auoreo.core.settings import ModelGroup, Settings
from auoreo.core.settings_gateway import GatewayTuning
from auoreo.gateway import aux
from auoreo.gateway.errors import FallbackTriggeredError, GatewayError
from auoreo.gateway.gateway import LiteLLMGateway, build_model_list, map_error, supported_pre_call_checks
from auoreo.gateway.types import LLMRequest, TextDelta, Usage


def _chunk(text: str = "", usage: Any = None, finish: str | None = None) -> Any:
    delta = SimpleNamespace(content=text, tool_calls=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if text or finish else []
    return SimpleNamespace(choices=choices, usage=usage)


async def _aiter(*chunks: Any, gap: float = 0.0) -> AsyncIterator[Any]:
    for c in chunks:
        if gap:
            await asyncio.sleep(gap)
        yield c


def _gw(home, **tuning: Any) -> LiteLLMGateway:  # type: ignore[no-untyped-def]
    st = Settings(
        home=home,
        models={
            "default": ModelGroup(models=["anthropic/claude-opus-5-5"]),
            "small": ModelGroup(models=["openai/gpt-4o-mini"]),
        },
        fallbacks={"default": ["small"]},
        gateway_tuning=GatewayTuning(**tuning),
    )
    gw = LiteLLMGateway(st, lambda n: "sk-test" if n.endswith("_API_KEY") else None)
    gw.variants = []
    return gw


def _overloaded() -> Exception:
    return lx.InternalServerError(
        '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', "anthropic", "m"
    )


def _req(purpose: str | None = None, **kw: Any) -> LLMRequest:
    meta = {"session_id": "s1", "run_id": "run-7"}
    if purpose:
        meta["purpose"] = purpose
    return LLMRequest(model="default", messages=({"role": "user", "content": "hi"},), metadata=meta, **kw)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_s: float) -> None:
        return None

    monkeypatch.setattr("auoreo.gateway.gateway._backoff_sleep", instant)
    aux.clear_failures()


async def test_three_overloads_on_foreground_fall_back_to_next_group(home, caplog) -> None:  # type: ignore[no-untyped-def]
    gw = _gw(home)
    trace: list[str] = []

    async def acompletion(**kw: Any) -> Any:
        trace.append(kw["model"])
        if kw["model"] == "default":
            raise _overloaded()
        return _aiter(_chunk("from small"), _chunk(finish="stop"))

    gw._router = SimpleNamespace(acompletion=acompletion)
    items = [i async for i in gw.stream(_req())]
    assert trace == ["default", "default", "default", "small"]
    assert "".join(i.text for i in items if isinstance(i, TextDelta)) == "from small"
    assert "falling back to small" in caplog.text


async def test_overload_with_no_fallback_raises_fallback_triggered(home) -> None:  # type: ignore[no-untyped-def]
    gw = _gw(home)
    gw.settings.fallbacks = {}

    async def acompletion(**kw: Any) -> Any:
        raise _overloaded()

    gw._router = SimpleNamespace(acompletion=acompletion)
    with pytest.raises(FallbackTriggeredError) as info:
        async for _ in gw.stream(_req()):
            pass
    assert isinstance(info.value, GatewayError) and info.value.streak == 3 and not info.value.retryable


async def test_background_calls_fail_fast(home) -> None:  # type: ignore[no-untyped-def]
    gw = _gw(home)
    calls: list[str] = []

    async def acompletion(**kw: Any) -> Any:
        calls.append(kw["model"])
        raise _overloaded()

    gw._router = SimpleNamespace(acompletion=acompletion)
    with pytest.raises(GatewayError) as info:
        async for _ in gw.stream(_req("title")):
            pass
    assert calls == ["default"] and info.value.overloaded and info.value.code == "RATE_LIMIT"
    assert aux._recently_failed("default", 60)  # lazy aux probe remembers it


async def test_auth_recovery_rereads_rotated_key_once(home) -> None:  # type: ignore[no-untyped-def]
    store = {"ANTHROPIC_API_KEY": "sk-old", "OPENAI_API_KEY": "sk-o"}
    st = Settings(
        home=home, models={"default": ModelGroup(models=["anthropic/claude-opus-5-5"])}, fallbacks={}
    )
    gw = LiteLLMGateway(st, store.get)
    gw.variants = []
    keys: list[str] = []

    async def acompletion(**kw: Any) -> Any:
        key = next(e for e in gw._model_list if e["model_name"] == "default")["litellm_params"]["api_key"]
        keys.append(key)
        if key == "sk-old":
            raise lx.AuthenticationError("invalid x-api-key", "anthropic", "m")
        return _aiter(_chunk("ok"), _chunk(finish="stop"))

    gw._router = SimpleNamespace(acompletion=acompletion)
    original = gw._make_router
    gw._make_router = lambda: SimpleNamespace(acompletion=acompletion)  # type: ignore[method-assign]
    store["ANTHROPIC_API_KEY"] = "sk-new"  # rotated while running
    items = [i async for i in gw.stream(_req())]
    assert keys == ["sk-old", "sk-new"] and any(isinstance(i, TextDelta) for i in items)
    keys.clear()  # a 401 with an unchanged secret store is not retried
    gw._model_list[0]["litellm_params"]["api_key"] = "sk-old"
    store["ANTHROPIC_API_KEY"] = "sk-old"
    with pytest.raises(GatewayError) as info:
        async for _ in gw.stream(_req()):
            pass
    assert info.value.code == "AUTH" and keys == ["sk-old"]
    gw._make_router = original  # type: ignore[method-assign]


def test_error_cause_is_redacted_and_truncated() -> None:
    secret = "sk-ant-api03-" + "A" * 40
    exc = lx.BadRequestError(f"bad key {secret} " + "x" * 5000, "m", "anthropic")
    err = map_error(exc)
    assert err.cause is not None and len(err.cause) <= 2048
    assert secret not in err.cause and secret not in err.message
    over = map_error(_overloaded())
    assert over.overloaded and over.code == "RATE_LIMIT" and "overloaded_error" in (over.cause or "")


def test_router_options_and_openrouter_headers(home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    st = Settings(
        home=home,
        models={
            "default": ModelGroup(models=["openrouter/a/b", "openrouter/c/d"]),
            "small": ModelGroup(models=["openai/gpt-4o-mini"]),
        },
        gateway_tuning=GatewayTuning(
            content_policy_fallbacks={"default": ["small"]}, weights={"default": [3, 1]}
        ),
    )
    ml = build_model_list(st, lambda _n: None)
    assert [e["litellm_params"].get("weight") for e in ml] == [3, 1, None]
    assert ml[0]["litellm_params"]["extra_headers"] == {
        "HTTP-Referer": "https://github.com/auoreo/auoreo",
        "X-Title": "AUOREO",
    }
    assert "extra_headers" not in ml[2]["litellm_params"]
    assert "session_affinity" in supported_pre_call_checks()  # introspected (and cached)
    real = litellm.Router
    captured: dict[str, Any] = {}

    def spy(**kw: Any) -> Any:
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(litellm, "Router", spy)
    LiteLLMGateway(st, lambda _n: None)
    assert captured["content_policy_fallbacks"] == [{"default": ["small"]}]
    assert captured["enable_weighted_failover"] is True
    assert captured["optional_pre_call_checks"] == ["session_affinity"]
    assert captured["num_retries"] == 0


async def test_wire_request_has_cache_breakpoints_turn_state_and_effort(home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AUOREO_EFFORT", raising=False)
    gw = _gw(home)
    gw.settings.models["default"].max_input_tokens = 10_000
    seen: list[dict[str, Any]] = []

    async def fake_open(kwargs: dict[str, Any]) -> Any:
        seen.append(kwargs)
        return _aiter(_chunk("ok"), _chunk(finish="stop"))

    monkeypatch.setattr(gw, "_open", fake_open)
    tool = {"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}
    req = LLMRequest(
        model="default",
        messages=({"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}),
        tools=(tool,),
        reasoning_effort="max",
        max_tokens=200_000,
        metadata={"session_id": "s1", "run_id": "run-7"},
    )
    _ = [i async for i in gw.stream(req)]
    sent = seen[0]
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["reasoning_effort"] == "max"  # Opus-class: no max→high clamp
    assert sent["extra_headers"] == {"x-auoreo-turn-state": "run-7"}
    assert sent["metadata"]["x-auoreo-turn-state"] == "run-7" and sent["metadata"]["session_id"] == "s1"
    assert 3000 <= sent["max_tokens"] <= 10_000 - 1000  # window − prompt − overflow buffer
    no_cache = LLMRequest(model="default", messages=req.messages, prompt_caching=False)
    _ = [i async for i in gw.stream(no_cache)]
    assert "cache_control" not in str(seen[1])


async def test_usage_details_flow_to_stream_ledger_and_bandit(home, services) -> None:  # type: ignore[no-untyped-def]
    from auoreo.core.usage import UsageLedger
    from auoreo.policy.bandit import signals_of

    gw = _gw(home)
    usage = SimpleNamespace(
        prompt_tokens=5000,
        completion_tokens=400,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4096),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )

    async def acompletion(**kw: Any) -> Any:
        return _aiter(_chunk("ok"), _chunk(finish="stop"), _chunk(usage=usage))

    gw._router = SimpleNamespace(acompletion=acompletion)
    got = [i async for i in gw.stream(_req())]
    u = next(i for i in got if isinstance(i, Usage))
    assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens, u.reasoning_tokens) == (
        5000,
        400,
        4096,
        150,
    )
    row = await UsageLedger(services.db).record("s1", "r1", "default", u)
    assert row.cached_tokens == 4096
    assert signals_of("completed", [row], (0.0, 0.0), 0)["cache_hit"] == 1


async def test_stream_idle_timeout_is_transport(home) -> None:  # type: ignore[no-untyped-def]
    gw = _gw(home, stream_idle_timeout_s=0.05)

    async def acompletion(**kw: Any) -> Any:
        return _aiter(_chunk("a"), _chunk("b"), gap=0.5)

    gw._router = SimpleNamespace(acompletion=acompletion)
    with pytest.raises(GatewayError) as info:
        async for _ in gw.stream(_req()):
            pass
    assert info.value.code == "TRANSPORT" and "idle" in info.value.message


async def test_variant_lookup_uses_primary_deployment(home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    gw = _gw(home)
    gw.variants = None  # load prompts/variants.yaml from the definition roots
    seen: list[dict[str, Any]] = []

    async def fake_open(kwargs: dict[str, Any]) -> Any:
        seen.append(kwargs)
        return _aiter(_chunk("ok"), _chunk(finish="stop"))

    monkeypatch.setattr(gw, "_open", fake_open)
    _ = [i async for i in gw.stream(_req(temperature=0.4))]
    assert "temperature" not in seen[0]  # Opus 5.x row omits sampling params
