"""Provider registry for Settings → Models (no `litellm` here; HTTP is in `provider_fetch`).

Each `ProviderSpec` names the provider, the secret its first key is stored under (the LiteLLM
env name, so the gateway's credential lookup finds it with no `.env`), the LiteLLM model prefix
(`<prefix><model id>` is what `settings.model` gets), how to list its models (URL, auth style,
parser) and the key shapes `detect_provider()` recognises. Endpoints (checked 2026-10-27):

- OpenAI `GET https://api.openai.com/v1/models` (Bearer); Groq `…/openai/v1/models`, xAI
  `https://api.x.ai/v1/models`, Mistral `https://api.mistral.ai/v1/models`, DeepSeek
  `https://api.deepseek.com/models`, Together `https://api.together.ai/v1/models`, Fireworks
  `https://api.fireworks.ai/inference/v1/models` — OpenAI-compatible lists (Bearer).
- Anthropic `GET https://api.anthropic.com/v1/models` (`x-api-key` + `anthropic-version`,
  paginated `has_more` / `last_id` → `after_id`).
- OpenRouter `GET https://openrouter.ai/api/v1/models` (public catalog) — the key itself is
  checked with `GET /api/v1/key` first, since the catalog answers without one.
- Gemini `GET https://generativelanguage.googleapis.com/v1beta/models?key=…` (`nextPageToken`).
- Cohere `GET https://api.cohere.com/v1/models` (Bearer, `next_page_token`, `endpoints`).
- Ollama `GET {base}/api/tags` (no key). Azure OpenAI: the key + endpoint + api version are
  validated with `GET {endpoint}/openai/models?api-version=…` (`api-key` header); the
  deployment names are entered by hand (Azure has no data-plane "list deployments").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Auth = Literal["bearer", "x-api-key", "query-key", "api-key", "none"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    secret: str | None
    """Secret-store name of the first key (LiteLLM's env name); None = keyless (ollama)."""
    prefix: str
    """LiteLLM model prefix: `settings.model = prefix + model id`."""
    list_url: str
    auth: Auth
    parser: str
    key_prefixes: tuple[str, ...] = ()
    needs_key: bool = True
    base_url_secret: str | None = None
    """Where a connection's base URL / endpoint is stored for LiteLLM (`*_API_BASE`)."""
    default_base_url: str | None = None
    version_secret: str | None = None
    default_api_version: str | None = None
    manual_models: bool = False
    """Azure: deployments are typed in, not listed."""
    validate_url: str | None = None
    """Key check when the list endpoint does not need a key (OpenRouter)."""
    docs_url: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def needs_base_url(self) -> bool:
        return self.base_url_secret is not None

    @property
    def needs_api_version(self) -> bool:
        return self.version_secret is not None


_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "openrouter", "OpenRouter", "OPENROUTER_API_KEY", "openrouter/",
        "https://openrouter.ai/api/v1/models", "bearer", "openrouter", ("sk-or-",),
        validate_url="https://openrouter.ai/api/v1/key",
        docs_url="https://openrouter.ai/docs/api/api-reference/models/get-models",
    ),
    ProviderSpec(
        "anthropic", "Anthropic", "ANTHROPIC_API_KEY", "anthropic/",
        "https://api.anthropic.com/v1/models", "x-api-key", "anthropic", ("sk-ant-",),
        extra_headers={"anthropic-version": "2023-06-01"},
        docs_url="https://docs.anthropic.com/en/api/models-list",
    ),
    ProviderSpec(
        "openai", "OpenAI", "OPENAI_API_KEY", "openai/",
        "https://api.openai.com/v1/models", "bearer", "openai",
        ("sk-proj-", "sk-svcacct-", "sk-admin-"),
        docs_url="https://platform.openai.com/docs/api-reference/models/list",
    ),
    ProviderSpec(
        "gemini", "Google Gemini", "GEMINI_API_KEY", "gemini/",
        "https://generativelanguage.googleapis.com/v1beta/models", "query-key", "gemini", ("AIza",),
        docs_url="https://ai.google.dev/api/models#method:-models.list",
    ),
    ProviderSpec(
        "groq", "Groq", "GROQ_API_KEY", "groq/",
        "https://api.groq.com/openai/v1/models", "bearer", "openai", ("gsk_",),
        docs_url="https://console.groq.com/docs/models",
    ),
    ProviderSpec(
        "xai", "xAI", "XAI_API_KEY", "xai/", "https://api.x.ai/v1/models", "bearer", "openai", ("xai-",),
        docs_url="https://docs.x.ai/docs/api-reference#list-models",
    ),
    ProviderSpec(
        "mistral", "Mistral", "MISTRAL_API_KEY", "mistral/",
        "https://api.mistral.ai/v1/models", "bearer", "mistral",
        docs_url="https://docs.mistral.ai/api/#tag/models",
    ),
    ProviderSpec(
        "deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek/",
        "https://api.deepseek.com/models", "bearer", "openai",
        docs_url="https://api-docs.deepseek.com/api/list-models",
    ),
    ProviderSpec(
        "together", "Together AI", "TOGETHERAI_API_KEY", "together_ai/",
        "https://api.together.ai/v1/models", "bearer", "together", ("tgp_",),
        docs_url="https://docs.together.ai/reference/models-1",
    ),
    ProviderSpec(
        "fireworks", "Fireworks AI", "FIREWORKS_AI_API_KEY", "fireworks_ai/",
        "https://api.fireworks.ai/inference/v1/models", "bearer", "openai", ("fw_", "fw-"),
        docs_url="https://docs.fireworks.ai/api-reference/list-models",
    ),
    ProviderSpec(
        "cohere", "Cohere", "COHERE_API_KEY", "cohere/",
        "https://api.cohere.com/v1/models", "bearer", "cohere",
        docs_url="https://docs.cohere.com/reference/list-models",
    ),
    ProviderSpec(
        "ollama", "Ollama (local)", None, "ollama/", "{base}/api/tags", "none", "ollama",
        needs_key=False, base_url_secret="OLLAMA_API_BASE", default_base_url="http://localhost:11434",
        docs_url="https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models",
    ),
    ProviderSpec(
        "azure", "Azure OpenAI", "AZURE_API_KEY", "azure/",
        "{base}/openai/models?api-version={version}", "api-key", "azure",
        base_url_secret="AZURE_API_BASE", version_secret="AZURE_API_VERSION",
        default_api_version="2024-10-21", manual_models=True,
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/reference",
    ),
)  # fmt: skip
PROVIDERS: dict[str, ProviderSpec] = {s.id: s for s in _SPECS}
"""Provider id → spec, in display order."""
_PLAIN_SK = re.compile(r"^sk-[A-Za-z0-9_-]{16,}$")
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_ALNUM = re.compile(r"^[A-Za-z0-9]{30,90}$")
AMBIGUOUS_SK = ("openai", "deepseek")
"""A bare `sk-…` key: OpenAI legacy user keys and DeepSeek keys share the shape."""


def get(provider: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise KeyError(f"unknown provider '{provider}'") from None


def detect_provider(key: str) -> dict[str, object]:
    """`{candidates: [provider ids], confident: bool}` from the key's shape alone (no network).

    A distinctive prefix (`sk-or-`, `sk-ant-`, `sk-proj-`, `gsk_`, `xai-`, `AIza`, `fw_`,
    `tgp_`) is confident; a bare `sk-` key could be OpenAI or DeepSeek; prefix-less keys could
    be Mistral, Cohere, Azure or Together — the UI then asks. An empty key means Ollama."""
    key = key.strip()
    if not key:
        return {"candidates": ["ollama"], "confident": False}
    for spec in sorted(_SPECS, key=lambda s: -max((len(p) for p in s.key_prefixes), default=0)):
        if any(key.startswith(p) for p in spec.key_prefixes):
            return {"candidates": [spec.id], "confident": True}
    if _PLAIN_SK.match(key):
        return {"candidates": list(AMBIGUOUS_SK), "confident": False}
    if _HEX32.match(key):
        return {"candidates": ["azure", "mistral", "together", "cohere"], "confident": False}
    if _ALNUM.match(key):
        return {"candidates": ["mistral", "cohere", "together", "azure"], "confident": False}
    keyed = [s.id for s in _SPECS if s.needs_key]
    return {"candidates": keyed, "confident": False}


def litellm_id(provider: str, model_id: str) -> str:
    """`<prefix><model id>` — the id `settings.model` and the gateway use."""
    return get(provider).prefix + model_id


__all__ = ["AMBIGUOUS_SK", "PROVIDERS", "ProviderSpec", "detect_provider", "get", "litellm_id"]
