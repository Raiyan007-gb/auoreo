"""Settings → Models: key-shape detection and every provider's model-list parser against recorded
fixture JSON (`tests/fixtures/providers/`, no network: pytest_httpx answers)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.gateway import providers as reg
from auoreo.gateway.provider_fetch import ProviderFetchError, list_models

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
KEY = "test-key-0123456789abcdefghij"


def fx(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("key", "candidates", "confident"),
    [
        ("sk-or-v1-0123456789abcdef0123456789abcdef", ["openrouter"], True),
        ("sk-ant-api03-0123456789abcdefghij", ["anthropic"], True),
        ("sk-proj-0123456789abcdefghijklmnop", ["openai"], True),
        ("sk-svcacct-0123456789abcdefghijkl", ["openai"], True),
        ("sk-0123456789abcdef0123456789abcdef", ["openai", "deepseek"], False),
        ("gsk_0123456789abcdefghijklmnop", ["groq"], True),
        ("xai-0123456789abcdefghijklmnop", ["xai"], True),
        ("AIzaSyA0123456789abcdefghijklmnopqrs", ["gemini"], True),
        ("fw_0123456789abcdefghijkl", ["fireworks"], True),
        ("tgp_v1_0123456789abcdefghijkl", ["together"], True),
        ("0123456789abcdef0123456789abcdef", ["azure", "mistral", "together", "cohere"], False),
        ("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd", ["mistral", "cohere", "together", "azure"], False),
        ("", ["ollama"], False),
    ],
)
def test_detect_provider(key: str, candidates: list[str], confident: bool) -> None:
    assert reg.detect_provider(key) == {"candidates": candidates, "confident": confident}


def test_registry_covers_the_brief() -> None:
    assert list(reg.PROVIDERS) == [
        "openrouter", "anthropic", "openai", "gemini", "groq", "xai", "mistral",
        "deepseek", "together", "fireworks", "cohere", "ollama", "azure",
    ]  # fmt: skip
    assert reg.PROVIDERS["together"].secret == "TOGETHERAI_API_KEY"
    assert reg.PROVIDERS["fireworks"].prefix == "fireworks_ai/"
    assert (
        reg.litellm_id("openrouter", "anthropic/claude-sonnet-4.5")
        == "openrouter/anthropic/claude-sonnet-4.5"
    )
    assert reg.PROVIDERS["ollama"].secret is None and reg.PROVIDERS["azure"].manual_models


def _kinds(models: list[Any]) -> dict[str, str]:
    return {m.id: m.kind for m in models}


async def test_openai_parser_and_bearer(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://api.openai.com/v1/models", json=fx("openai"))
    models = await list_models(reg.get("openai"), KEY)
    assert _kinds(models) == {
        "gpt-5": "chat",
        "text-embedding-3-small": "embedding",
        "whisper-1": "other",
        "tts-1": "other",
        "dall-e-3": "other",
    }
    assert httpx_mock.get_request().headers["Authorization"] == f"Bearer {KEY}"  # type: ignore[union-attr]


async def test_anthropic_paginates_with_x_api_key(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://api.anthropic.com/v1/models?limit=1000", json=fx("anthropic_p1"))
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/models?limit=1000&after_id=claude-opus-5-5", json=fx("anthropic_p2")
    )
    models = await list_models(reg.get("anthropic"), KEY)
    assert [(m.id, m.name) for m in models] == [
        ("claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ]
    req = httpx_mock.get_requests()[0]
    assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == "2023-06-01"


async def test_openrouter_checks_the_key_then_reads_prices(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://openrouter.ai/api/v1/key", json=fx("openrouter_key"))
    httpx_mock.add_response(url="https://openrouter.ai/api/v1/models", json=fx("openrouter"))
    models = {m.id: m for m in await list_models(reg.get("openrouter"), KEY)}
    sonnet = models["anthropic/claude-sonnet-4.5"]
    assert sonnet.kind == "chat" and sonnet.context_window == 1_000_000
    assert sonnet.input_price == 3.0 and sonnet.output_price == 15.0
    assert set(sonnet.capabilities) == {"tools", "reasoning", "vision"}
    assert models["openai/text-embedding-3-large"].kind == "embedding"
    assert models["openai/gpt-image-1"].kind == "other"


async def test_openrouter_bad_key_is_invalid(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://openrouter.ai/api/v1/key",
        status_code=401,
        json={"error": {"code": 401, "message": "No auth"}},
    )
    with pytest.raises(ProviderFetchError) as err:
        await list_models(reg.get("openrouter"), KEY)
    assert err.value.status == "invalid" and err.value.message == "No auth"


async def test_gemini_query_key_and_generation_methods(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY}&pageSize=1000",
        json=fx("gemini"),
    )
    models = {m.id: m for m in await list_models(reg.get("gemini"), KEY)}
    assert models["gemini-2.5-pro"].kind == "chat" and models["gemini-2.5-pro"].context_window == 1048576
    assert models["gemini-embedding-001"].kind == "embedding"
    assert models["imagen-4.0-generate-001"].kind == "other"


@pytest.mark.parametrize(
    ("provider", "url", "fixture", "expected"),
    [
        ("groq", "https://api.groq.com/openai/v1/models", "groq",
         {"llama-3.3-70b-versatile": "chat", "whisper-large-v3": "other"}),
        ("xai", "https://api.x.ai/v1/models", "xai", {"grok-4": "chat"}),
        ("deepseek", "https://api.deepseek.com/models", "deepseek",
         {"deepseek-chat": "chat", "deepseek-reasoner": "chat"}),
        ("fireworks", "https://api.fireworks.ai/inference/v1/models", "fireworks",
         {"accounts/fireworks/models/llama-v3p3-70b-instruct": "chat"}),
        ("mistral", "https://api.mistral.ai/v1/models", "mistral",
         {"mistral-large-latest": "chat", "mistral-embed": "embedding"}),
        ("together", "https://api.together.ai/v1/models", "together",
         {"meta-llama/Llama-3.3-70B-Instruct-Turbo": "chat", "BAAI/bge-large-en-v1.5": "embedding",
          "black-forest-labs/FLUX.1-schnell": "other"}),
        ("cohere", "https://api.cohere.com/v1/models?page_size=1000", "cohere",
         {"command-a-03-2025": "chat", "embed-v4.0": "embedding", "rerank-v3.5": "other"}),
    ],
)  # fmt: skip
async def test_openai_compatible_and_native_parsers(
    httpx_mock: HTTPXMock, provider: str, url: str, fixture: str, expected: dict[str, str]
) -> None:
    httpx_mock.add_response(url=url, json=fx(fixture))
    models = await list_models(reg.get(provider), KEY)
    assert _kinds(models) == expected


async def test_parser_details(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://api.together.ai/v1/models", json=fx("together"))
    llama = (await list_models(reg.get("together"), KEY))[0]
    assert (llama.input_price, llama.output_price, llama.context_window) == (0.88, 0.88, 131072)
    httpx_mock.add_response(url="https://api.groq.com/openai/v1/models", json=fx("groq"))
    assert (await list_models(reg.get("groq"), KEY))[0].context_window == 131072


async def test_ollama_lists_tags_without_a_key(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://box.lan:11434/api/tags", json=fx("ollama"))
    models = await list_models(reg.get("ollama"), None, base_url="http://box.lan:11434")
    assert _kinds(models) == {"llama3.2:latest": "chat", "nomic-embed-text:latest": "embedding"}
    assert "Authorization" not in httpx_mock.get_request().headers  # type: ignore[union-attr]


async def test_azure_validates_with_api_key_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://res.openai.azure.com/openai/models?api-version=2024-10-21", json=fx("azure")
    )
    models = await list_models(reg.get("azure"), KEY, base_url="https://res.openai.azure.com/")
    assert models == []  # deployments are entered by hand
    assert httpx_mock.get_request().headers["api-key"] == KEY  # type: ignore[union-attr]


async def test_errors_are_redacted_and_keyless(httpx_mock: HTTPXMock) -> None:
    body = {"error": {"message": f"Incorrect API key provided: {KEY}", "type": "invalid_request_error"}}
    httpx_mock.add_response(url="https://api.openai.com/v1/models", status_code=401, json=body)
    with pytest.raises(ProviderFetchError) as err:
        await list_models(reg.get("openai"), KEY)
    assert err.value.status == "invalid" and KEY not in err.value.message and "***" in err.value.message
    httpx_mock.add_response(url="https://api.openai.com/v1/models", status_code=503, text="upstream down")
    with pytest.raises(ProviderFetchError) as err:
        await list_models(reg.get("openai"), KEY)
    assert err.value.status == "error" and "503" in err.value.message
    httpx_mock.add_exception(httpx.ConnectError("offline"), url="https://api.openai.com/v1/models")
    with pytest.raises(ProviderFetchError) as err:
        await list_models(reg.get("openai"), KEY)
    assert err.value.status == "error" and "cannot reach api.openai.com" in err.value.message


async def test_test_override_repoints_the_host(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://127.0.0.1:9/v1/models", json=fx("openai"))
    models = await list_models(reg.get("openai"), KEY, override="http://127.0.0.1:9")
    assert len(models) == 5
