"""Universal provider keys: `<provider>/<model id>` picks the key by LiteLLM's naming."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core import env_store
from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.gateway import (
    build_model_list,
    credential_params,
    credential_status,
    provider_of,
)


def store(**keys: str):  # type: ignore[no-untyped-def]
    return lambda name: keys.get(name)


@pytest.mark.parametrize(
    ("model", "keys", "expected"),
    [
        ("openrouter/anthropic/claude-sonnet-4.5", {"OPENROUTER_API_KEY": "or"}, {"api_key": "or"}),
        ("anthropic/claude-sonnet-5", {"ANTHROPIC_API_KEY": "an"}, {"api_key": "an"}),
        ("openai/gpt-5", {"OPENAI_API_KEY": "oa"}, {"api_key": "oa"}),
        ("gemini/gemini-2.5-pro", {"GEMINI_API_KEY": "ge"}, {"api_key": "ge"}),
        ("together_ai/meta-llama/x", {"TOGETHERAI_API_KEY": "tg"}, {"api_key": "tg"}),
        (
            "azure/my-deploy",
            {"AZURE_API_KEY": "az", "AZURE_API_BASE": "https://x", "AZURE_API_VERSION": "2025"},
            {"api_key": "az", "api_base": "https://x", "api_version": "2025"},
        ),
        # unknown to LiteLLM's env table → `<PREFIX>_API_KEY` convention
        ("zai/glm-4.6", {"ZAI_API_KEY": "z"}, {"api_key": "z"}),
        # the wrong provider's key is never sent
        ("anthropic/claude-sonnet-5", {"OPENROUTER_API_KEY": "or"}, {}),
    ],
)
def test_key_follows_provider_prefix(model: str, keys: dict[str, str], expected: dict[str, str]) -> None:
    assert credential_params(model, store(**keys)) == expected


def test_provider_of() -> None:
    assert provider_of("openrouter/anthropic/claude-sonnet-4.5") == "openrouter"
    assert provider_of("default") == ""


def test_model_list_mixes_providers(home: Path) -> None:
    st = Settings(
        home=home,
        models={"default": ModelGroup(models=["anthropic/claude-sonnet-5", "openai/gpt-5"])},
    )
    ml = build_model_list(st, store(ANTHROPIC_API_KEY="an", OPENAI_API_KEY="oa"))
    assert [e["litellm_params"]["api_key"] for e in ml] == ["an", "oa"]


def test_group_api_base_beats_env_base(home: Path) -> None:
    st = Settings(home=home, models={"default": ModelGroup(models=["ollama/llama3"], api_base="http://h:1")})
    ml = build_model_list(st, store(OLLAMA_API_BASE="http://env:2"))
    assert ml[0]["litellm_params"]["api_base"] == "http://h:1"


def test_credential_status_names_missing_key() -> None:
    ok, detail = credential_status("anthropic/claude-sonnet-5", store())
    assert not ok and "ANTHROPIC_API_KEY" in detail
    ok, detail = credential_status("gemini/gemini-2.5-pro", store(GOOGLE_API_KEY="g"))
    assert ok, detail
    ok, detail = credential_status("anthropic/claude-sonnet-5", store(ANTHROPIC_API_KEY="a"))
    assert ok and "ANTHROPIC_API_KEY" in detail


def test_auoreo_model_defines_groups(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_MODEL", "anthropic/claude-sonnet-5")
    st = Settings(home=home)
    assert st.models["default"].models == ["anthropic/claude-sonnet-5"]
    assert st.models["small"].models == ["anthropic/claude-sonnet-5"]
    monkeypatch.setenv("AUOREO_SMALL_MODEL", "anthropic/claude-haiku-5")
    assert Settings(home=home).models["small"].models == ["anthropic/claude-haiku-5"]


def test_explicit_models_win_over_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_MODEL", "anthropic/claude-sonnet-5")
    st = Settings(home=home, models={"default": ModelGroup(models=["openai/gpt-5"])})
    assert st.models["default"].models == ["openai/gpt-5"]
    env_store.write(home, {"models": {"default": {"models": ["xai/grok-4"]}}})
    assert Settings(home=home).models["default"].models == ["xai/grok-4"]


def test_group_name_model_leaves_groups(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_MODEL", "small")
    assert Settings(home=home).models["default"].models == ["openrouter/anthropic/claude-sonnet-4.5"]


def test_embed_override(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_EMBED_MODEL", "openai/text-embedding-3-large")
    monkeypatch.setenv("AUOREO_EMBED_DIMS", "3072")
    st = Settings(home=home)
    assert (st.memory.embed_model, st.memory.dims) == ("openai/text-embedding-3-large", 3072)


def test_blank_env_values_are_ignored(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_EMBED_DIMS", "")
    assert Settings(home=home).memory.dims == 1536
