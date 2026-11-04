"""LiteLLM credential names → completion kwargs (the litellm-free half of `gateway.py`).

`gateway.py` asks LiteLLM which env names a model still needs (`env_names`); this module maps
those names to kwargs and fills them from the secret lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

# LiteLLM env-var name → completion kwarg. Names come from `litellm.validate_environment`,
# so any provider LiteLLM knows works by prefix: `anthropic/…` → ANTHROPIC_API_KEY,
# `gemini/…` → GEMINI_API_KEY | GOOGLE_API_KEY, `azure/…` → AZURE_API_KEY + _BASE + _VERSION.
_SUFFIX_PARAM = {"_API_KEY": "api_key", "_API_BASE": "api_base", "_API_VERSION": "api_version"}
_EXACT_PARAM = {"VERTEXAI_PROJECT": "vertex_project", "VERTEXAI_LOCATION": "vertex_location"}
# Endpoint secrets a Settings → Models connection stores, passed even when LiteLLM does not list them.
_ENDPOINT_SECRETS = {"OLLAMA": ("OLLAMA_API_BASE",), "AZURE": ("AZURE_API_BASE", "AZURE_API_VERSION")}


def param_for(name: str) -> str | None:
    if name in _EXACT_PARAM:
        return _EXACT_PARAM[name]
    return next((p for sfx, p in _SUFFIX_PARAM.items() if name.endswith(sfx)), None)


def params(prefix: str, names: Iterable[str], secret: Callable[[str], str | None]) -> dict[str, str]:
    """Kwargs for the env `names` LiteLLM listed plus the `<PREFIX>_API_KEY` convention and the
    endpoint secrets; `prefix` is the upper-cased provider. Keys only in the secret store (not
    in the env) are passed explicitly, so LiteLLM never needs them in `os.environ`."""
    out: dict[str, str] = {}
    for name in names:
        param = param_for(name)
        if param and param not in out and (value := secret(name)):
            out[param] = value
    if prefix and "api_key" not in out and (value := secret(f"{prefix}_API_KEY")):
        out["api_key"] = value
    for name in _ENDPOINT_SECRETS.get(prefix, ()):  # Ollama / Azure endpoints from Settings → Models
        param = param_for(name)
        if param and param not in out and (value := secret(name)):
            out[param] = value
    return out
