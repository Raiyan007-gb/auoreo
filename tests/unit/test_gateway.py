from __future__ import annotations

import pytest
from litellm import exceptions as lx

from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.gateway import LiteLLMGateway, build_model_list, map_error
from auoreo.gateway.resolve import resolve_model
from auoreo.gateway.types import LLMRequest, TextDelta, Usage


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (lx.ContextWindowExceededError("too long", "m", "openai"), "CONTEXT"),
        (lx.AuthenticationError("bad", "openai", "m"), "AUTH"),
        (lx.RateLimitError("slow", "openai", "m"), "RATE_LIMIT"),
        (lx.NotFoundError("nope", "m", "openai"), "MODEL_NOT_FOUND"),
        (lx.BadRequestError("bad", "m", "openai"), "INVALID_REQUEST"),
        (lx.APIConnectionError("down", "openai", "m"), "TRANSPORT"),
    ],
)
def test_errors_map_by_type(exc: Exception, code: str) -> None:
    err = map_error(exc)
    assert err.code == code
    assert err.retryable == (code in {"RATE_LIMIT", "TRANSPORT"})


def test_model_list_from_groups_and_secret_injection(home) -> None:  # type: ignore[no-untyped-def]
    st = Settings(home=home, models={"default": ModelGroup(models=["openrouter/a/b", "openrouter/c/d"])})
    ml = build_model_list(st, lambda name: "sk-or-test" if name == "OPENROUTER_API_KEY" else None)
    assert [e["litellm_params"]["order"] for e in ml] == [1, 2]
    assert all(e["model_name"] == "default" and e["litellm_params"]["api_key"] == "sk-or-test" for e in ml)


def test_resolution_chain(home) -> None:  # type: ignore[no-untyped-def]
    st = Settings(home=home, model="small", aliases={"fast": "small"})
    assert resolve_model(st) == "small"
    assert resolve_model(st, agent_model="fast") == "small"
    assert resolve_model(st, flag="x/y", agent_model="fast") == "x/y"
    assert resolve_model(st, session_override="s", flag="x/y") == "s"


async def test_litellm_router_streams_mock_response(home) -> None:  # type: ignore[no-untyped-def]
    st = Settings(home=home, models={"default": ModelGroup(models=["openai/gpt-4o-mini"])}, fallbacks={})
    gw = LiteLLMGateway(st, lambda _: None)
    gw._model_list[0]["litellm_params"]["mock_response"] = "hello from mock"
    gw._router = gw._make_router()
    items = [
        i async for i in gw.stream(LLMRequest(model="default", messages=({"role": "user", "content": "hi"},)))
    ]
    assert "".join(i.text for i in items if isinstance(i, TextDelta)) == "hello from mock"
    assert any(isinstance(i, Usage) for i in items)
