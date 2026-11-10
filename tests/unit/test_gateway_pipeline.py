"""Request pipeline, effort vocab, usage parsing and alias resolution (G3, spec §10.6/§12)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.pipeline import (
    IMAGE_PLACEHOLDER,
    ShapeContext,
    clamp_max_tokens,
    family_of,
    map_effort,
    match_variant,
    reasoning_kwargs,
    resolve_effort,
    shape,
)
from auoreo.gateway.resolve import builtin_alias, expand_alias, primary_deployment, resolve_model
from auoreo.gateway.usage_parse import parse_usage
from auoreo.harness.manifests import Variant, VariantsFile

TOOL = {"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}


def _kwargs(**extra: Any) -> dict[str, Any]:
    return {
        "model": "default",
        "messages": [
            {"role": "system", "content": "stable system prompt"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok", "reasoning_content": "hmm", "thinking_blocks": [{}]},
            {
                "role": "user",
                "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {}}],
            },
        ],
        "tools": [TOOL, {**TOOL, "function": {**TOOL["function"], "name": "u"}}],
        **extra,
    }


def test_anthropic_cache_breakpoints_on_tools_system_and_last_user() -> None:
    out = shape(_kwargs(), ShapeContext(model="anthropic/claude-sonnet-5-5"))
    cc = {"type": "ephemeral"}
    assert "cache_control" not in out["tools"][0] and out["tools"][-1]["cache_control"] == cc
    assert out["messages"][0]["content"] == [
        {"type": "text", "text": "stable system prompt", "cache_control": cc}
    ]
    assert out["messages"][-1]["content"][-1]["cache_control"] == cc
    assert out["messages"][1]["content"] == "first"  # only the LAST user message is marked
    marks = str(out).count("cache_control")
    assert marks == 3 <= 4


CC = {"type": "ephemeral"}
CLAUDE = "anthropic/claude-sonnet-5-5"


def _tool_loop(**extra: Any) -> dict[str, Any]:
    """system, user, assistant(tool call), tool result: the shape of a step inside a tool turn."""
    kw = _kwargs(**extra)
    kw["messages"] = [
        {"role": "system", "content": "stable system prompt"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    return kw


def test_legacy_layout_is_the_default_and_marks_the_last_user_message() -> None:
    out = shape(_tool_loop(), ShapeContext(model=CLAUDE))
    assert out["messages"][1]["content"][-1]["cache_control"] == CC  # the turn's opening message
    assert "cache_control" not in out["messages"][3]  # the tool tail stays uncached
    assert str(out).count("cache_control") == 3


def test_tail_layout_adds_a_fourth_mark_on_the_tool_result() -> None:
    out = shape(_tool_loop(), ShapeContext(model=CLAUDE, cache_layout="tail"))
    assert out["tools"][-1]["cache_control"] == CC
    assert out["messages"][0]["content"][-1]["cache_control"] == CC
    assert out["messages"][1]["content"][-1]["cache_control"] == CC  # last user message kept
    assert out["messages"][3]["cache_control"] == CC  # role=tool: message-level key
    assert str(out).count("cache_control") == 4


def test_tail_layout_skips_unmarkable_tails() -> None:
    kw = _tool_loop()
    kw["messages"].append({"role": "assistant", "content": None})
    out = shape(kw, ShapeContext(model=CLAUDE, cache_layout="tail"))
    assert out["messages"][3]["cache_control"] == CC and "cache_control" not in out["messages"][4]
    thinking = _tool_loop()
    thinking["messages"].append(
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]},
    )
    out = shape(thinking, ShapeContext(model=CLAUDE, cache_layout="tail"))
    assert out["messages"][3]["cache_control"] == CC
    assert "cache_control" not in str(out["messages"][4])


def test_tail_layout_adds_no_fifth_mark_when_the_last_message_is_the_user() -> None:
    out = shape(_kwargs(), ShapeContext(model=CLAUDE, cache_layout="tail"))
    assert str(out).count("cache_control") == 3
    no_tools = _kwargs()
    no_tools.pop("tools", None)
    assert str(shape(no_tools, ShapeContext(model=CLAUDE, cache_layout="tail"))).count("cache_control") == 2


def test_tail_layout_leaves_other_families_and_inputs_alone() -> None:
    src = _tool_loop()
    assert "cache_control" not in str(shape(src, ShapeContext(model="openai/gpt-5", cache_layout="tail")))
    shape(src, ShapeContext(model=CLAUDE, cache_layout="tail"))
    assert "cache_control" not in str(src)


def test_caching_skipped_for_non_anthropic_or_disabled_and_inputs_untouched() -> None:
    src = _kwargs()
    assert "cache_control" not in str(shape(src, ShapeContext(model="openai/gpt-5")))
    assert "cache_control" not in str(shape(src, ShapeContext(model="anthropic/claude-x", caching=False)))
    assert "cache_control" not in str(src)
    ttl = shape(src, ShapeContext(model="openrouter/anthropic/claude-opus-5.5", cache_ttl="1h"))
    assert ttl["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_unsupported_parts_normalize_and_remap() -> None:
    v = Variant(
        block_binding="strip",
        omit_fields=["temperature"],
        rename_fields={"max_tokens": "max_completion_tokens"},
    )
    ctx = ShapeContext(model="openai/o3", variant=v, vision=False, omit=("top_p",))
    out = shape(_kwargs(temperature=0.4, top_p=1, max_tokens=10), ctx)
    assert out["messages"][-1]["content"][1] == {"type": "text", "text": IMAGE_PLACEHOLDER}
    assert "reasoning_content" not in out["messages"][2] and "thinking_blocks" not in out["messages"][2]
    assert "temperature" not in out and "top_p" not in out
    assert out["max_completion_tokens"] == 10 and "max_tokens" not in out
    renamed = shape(_kwargs(), ShapeContext(model="kimi-k2", variant=Variant(interleaved_field="reasoning")))
    assert renamed["messages"][2]["reasoning"] == "hmm"


@pytest.mark.parametrize(
    ("effort", "model", "vocab", "sent"),
    [
        ("max", "anthropic/claude-opus-5-5", None, "max"),  # Opus-class: no clamp
        ("max", "openrouter/anthropic/claude-sonnet-4.5", None, "high"),  # clamp off Opus-class
        ("max", "anthropic/claude-sonnet-5-5", {"max": "max"}, "max"),  # vocab says otherwise
        ("medium", "x/y", {"low": "minimal", "high": "high"}, "minimal"),  # nearest weaker
        ("low", "x/y", {"high": "high"}, None),  # nothing weaker → omit (fail-safe)
    ],
)
def test_effort_vocab(effort: str, model: str, vocab: dict[str, str] | None, sent: str | None) -> None:
    assert map_effort(effort, model, vocab) == sent


def test_vocab_list_shorthand_and_reasoning_tristate() -> None:
    assert Variant(reasoning_vocab=["low", "high"]).reasoning_vocab == {"low": "low", "high": "high"}
    m = "anthropic/claude-opus-5-5"
    assert reasoning_kwargs("high", m, Variant(reasoning="off")) == {}
    assert reasoning_kwargs(None, m, Variant(reasoning="auto")) == {}
    assert reasoning_kwargs(None, m, Variant(reasoning="on")) == {"reasoning_effort": "medium"}
    shown = reasoning_kwargs("max", m, Variant(thinking_display="hidden"))
    assert shown == {"reasoning_effort": "max", "thinking": {"type": "adaptive", "display": "omitted"}}
    assert "thinking" not in reasoning_kwargs("low", "openai/gpt-5", Variant(thinking_display="summary"))


def test_effort_resolution_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_EFFORT", raising=False)
    assert resolve_effort("low") == "low"
    assert resolve_effort("low", "high") == "high"
    monkeypatch.setenv("AUOREO_EFFORT", "max")
    assert resolve_effort("low", "high") == "max"
    monkeypatch.setenv("AUOREO_EFFORT", "bogus")
    assert resolve_effort(None, None) is None


def test_max_tokens_floor_and_overflow_buffer() -> None:
    assert clamp_max_tokens(8000, 128_000, 1000, 3000, 1000) == 8000
    assert clamp_max_tokens(8000, 10_000, 4000, 3000, 1000) == 5000  # window − prompt − buffer
    assert clamp_max_tokens(8000, 10_000, 9000, 3000, 1000) == 3000  # never below the floor
    assert clamp_max_tokens(2000, 10_000, 9500, 3000, 1000) == 2000  # never above the request
    assert clamp_max_tokens(None, 10, 10, 3000, 1000) is None


def test_bundled_variants_rows() -> None:
    import yaml

    from auoreo.core.paths import bundled_root

    rows = VariantsFile.model_validate(
        yaml.safe_load((bundled_root() / "prompts/variants.yaml").read_text())
    ).variants
    opus = match_variant(rows, "openrouter/anthropic/claude-opus-4.7")
    assert opus is not None and opus.reasoning_vocab == {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "max",
    }
    sonnet = match_variant(rows, "anthropic/claude-sonnet-5-5")
    assert sonnet is not None and "temperature" in sonnet.omit_fields and sonnet.reasoning_vocab is None
    gem = match_variant(rows, "gemini/gemini-3-pro")
    assert gem is not None and gem.reasoning_vocab and gem.reasoning_vocab["max"] == "high"
    assert (match_variant(rows, "openrouter/qwen/qwen3:free") or Variant()).reasoning == "off"
    assert (match_variant(rows, "openrouter/anthropic/claude-sonnet-4.5") or Variant()).temperature == 0.4
    assert family_of("bedrock/anthropic.claude-opus-5-5") == "anthropic"


def test_usage_parse_cached_and_reasoning_tokens() -> None:
    openai_shape = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=120),
    )
    u = parse_usage(openai_shape)
    assert u is not None and (u.cached_tokens, u.reasoning_tokens, u.completion_tokens) == (1024, 120, 300)
    anthropic_raw = {"prompt_tokens": 5000, "completion_tokens": 40, "cache_read_input_tokens": 4096}
    a = parse_usage(anthropic_raw)
    assert a is not None and a.cached_tokens == 4096
    separate = parse_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 50}}
    )
    assert separate is not None and separate.completion_tokens == 55  # reasoning folded into the total
    assert parse_usage(None) is None and parse_usage({"prompt_tokens": 0}) is None
    assert a.event_data()["cached_tokens"] == 4096


def test_builtin_aliases_follow_provider_and_user_aliases_win(home) -> None:  # type: ignore[no-untyped-def]
    st = Settings(home=home)  # default group is OpenRouter
    assert resolve_model(st, flag="opus") == "openrouter/anthropic/claude-opus-5.5"
    assert resolve_model(st, flag="haiku") == "openrouter/anthropic/claude-haiku-5.5"
    native = Settings(home=home, models={"default": ModelGroup(models=["anthropic/claude-sonnet-5-5"])})
    assert expand_alias(native, "sonnet") == "anthropic/claude-sonnet-5-5"
    assert expand_alias(native, "opusplan") == "anthropic/claude-opus-5-5"
    assert builtin_alias(native, "opus[1m]") == "anthropic/claude-opus-5-5[1m]"
    custom = Settings(home=home, aliases={"opus": "default"}, models={"sonnet": ModelGroup(models=["x/y"])})
    assert expand_alias(custom, "opus") == "default"
    assert expand_alias(custom, "sonnet") == "sonnet"  # a routing group named like an alias wins
    assert primary_deployment(custom, "sonnet") == "x/y"
    assert expand_alias(custom, "unknown/model") == "unknown/model"


# ------------------------------------------------------------------ M7 1.6: OpenAI cache key
def test_openai_cache_key_is_sent_only_with_a_key_and_caching_on() -> None:
    src = _kwargs()
    out = shape(src, ShapeContext(model="openai/gpt-5", cache_key="sess_1"))
    assert out["prompt_cache_key"] == "sess_1" and "prompt_cache_key" not in src
    assert out["messages"] == src["messages"] and "cache_control" not in str(out)
    assert shape(_kwargs(), ShapeContext(model="gpt-4o", cache_key="sess_1"))["prompt_cache_key"] == "sess_1"
    assert shape(_kwargs(), ShapeContext(model="azure/gpt-4o", cache_key="s"))["prompt_cache_key"] == "s"
    assert shape(src, ShapeContext(model="openai/gpt-5")) == src  # no key (flag off): unchanged
    off = shape(src, ShapeContext(model="openai/gpt-5", cache_key="sess_1", caching=False))
    assert "prompt_cache_key" not in off


def test_openai_cache_key_stays_off_other_families_and_unsupported_routes() -> None:
    for model in (CLAUDE, "gemini/gemini-2.5-pro", "moonshot/kimi-k2", "ollama/llama3"):
        assert "prompt_cache_key" not in shape(_kwargs(), ShapeContext(model=model, cache_key="sess_1"))
    # LiteLLM 1.106 lists the parameter for openai and azure only; elsewhere it would be dropped.
    routed = shape(_kwargs(), ShapeContext(model="openrouter/openai/gpt-5", cache_key="sess_1"))
    assert "prompt_cache_key" not in routed


def test_openai_cache_key_respects_a_caller_value_and_variant_omit() -> None:
    kept = shape(_kwargs(prompt_cache_key="mine"), ShapeContext(model="openai/gpt-5", cache_key="sess_1"))
    assert kept["prompt_cache_key"] == "mine"
    omitted = shape(
        _kwargs(), ShapeContext(model="openai/gpt-5", cache_key="sess_1", omit=("prompt_cache_key",))
    )
    assert "prompt_cache_key" not in omitted


@pytest.mark.parametrize(("flag", "expected"), [(False, None), (True, "sess_42")])
def test_gateway_sends_the_session_id_as_cache_key_behind_the_flag(
    home: Any, flag: bool, expected: str | None
) -> None:
    from auoreo.gateway.gateway import LiteLLMGateway
    from auoreo.gateway.types import LLMRequest

    st = Settings(
        home=home, models={"main": {"models": ["openai/gpt-5"]}}, m7={"token": {"openai_cache_key": flag}}
    )
    gw = LiteLLMGateway(st, lambda _n: None)
    msgs = ({"role": "user", "content": "hi"},)
    kw = gw._kwargs(LLMRequest(model="main", messages=msgs, metadata={"session_id": "sess_42"}), "main")
    assert kw.get("prompt_cache_key") == expected
    bare = gw._kwargs(LLMRequest(model="main", messages=msgs), "main")  # no session: no key
    assert "prompt_cache_key" not in bare
