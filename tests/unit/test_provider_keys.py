"""Universal provider keys: `<provider>/<model id>` picks the key by LiteLLM's naming."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auoreo.core.dotenv import load_env_files
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
    (home / "settings.json").write_text('{"models": {"default": {"models": ["xai/grok-4"]}}}')
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


def test_dotenv_loads_without_overriding(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text(
        "ANTHROPIC_API_KEY=from-project\nOPENAI_API_KEY=\nAUOREO_MODEL=anthropic/claude-sonnet-5\n"
    )
    (home / ".env").write_text("ANTHROPIC_API_KEY=from-home\nGROQ_API_KEY=from-home\n")
    monkeypatch.setenv("GROQ_API_KEY", "from-shell")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    try:
        loaded = load_env_files(proj)
        assert os.environ["ANTHROPIC_API_KEY"] == "from-project"  # project beats home
        assert os.environ["GROQ_API_KEY"] == "from-shell"  # real env always wins
        assert "OPENAI_API_KEY" not in os.environ  # blank placeholder skipped
        assert set(loaded) == {"ANTHROPIC_API_KEY", "AUOREO_MODEL"}
        assert Settings(home=home).models["default"].models == ["anthropic/claude-sonnet-5"]
    finally:
        for name in ("ANTHROPIC_API_KEY", "AUOREO_MODEL"):
            os.environ.pop(name, None)


@pytest.mark.parametrize(
    ("model", "has_temperature"),
    [
        ("anthropic/claude-sonnet-5", False),
        ("openrouter/anthropic/claude-opus-5", False),
        ("openai/gpt-5", False),
        ("openai/o3", False),
        ("anthropic/claude-sonnet-4.5", True),
        ("groq/llama-3.3-70b-versatile", True),
    ],
)
def test_fixed_temperature_models_omit_it(tmp_path: Path, model: str, has_temperature: bool) -> None:
    from auoreo.harness.discovery import default_roots
    from auoreo.harness.orchestrator import pick_variant
    from auoreo.harness.registry import Harness

    repo = Path(__file__).resolve().parents[2]
    h = Harness(default_roots(tmp_path / "h", tmp_path, repo))
    v = pick_variant(h, model)
    assert v is not None
    assert ("temperature" in v.omit_fields) is not has_temperature
