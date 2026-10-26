"""Provider usage → `Usage` (spec §10.8, §20.2). No `litellm` here.

LiteLLM normalises usage to the OpenAI shape, but cache/reasoning detail lives in
different places per provider:
- cached prompt tokens: `prompt_tokens_details.cached_tokens` (OpenAI, and Anthropic after
  LiteLLM's mapping) or the raw Anthropic `cache_read_input_tokens`;
- reasoning tokens: `completion_tokens_details.reasoning_tokens`;
- cache writes: Anthropic `cache_creation_input_tokens` or `prompt_tokens_details.cache_write_tokens`;
- `usage.cost`: OpenRouter's provider-reported USD cost (stored as-is, not estimated).
Reasoning tokens are part of `completion_tokens`; a provider that reports them separately
(completion < reasoning) has them added to the total.
"""

from __future__ import annotations

from typing import Any

from auoreo.gateway.types import Usage


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_usage(raw: Any) -> Usage | None:
    """A `Usage` from a LiteLLM/OpenAI usage object or dict; None when it carries no tokens."""
    prompt = _int(_get(raw, "prompt_tokens"))
    completion = _int(_get(raw, "completion_tokens"))
    if not (prompt or completion):
        return None
    cached = _int(_get(_get(raw, "prompt_tokens_details"), "cached_tokens")) or _int(
        _get(raw, "cache_read_input_tokens")
    )
    reasoning = _int(_get(_get(raw, "completion_tokens_details"), "reasoning_tokens"))
    if completion < reasoning:
        completion += reasoning
    written = _int(_get(raw, "cache_creation_input_tokens")) or _int(
        _get(_get(raw, "prompt_tokens_details"), "cache_write_tokens")
    )
    return Usage(
        prompt,
        completion,
        cost_usd=_cost(_get(raw, "cost")),
        cached_tokens=min(cached, prompt) if prompt else cached,
        reasoning_tokens=reasoning,
        cache_write_tokens=written,
    )


def _cost(value: Any) -> float | None:
    """OpenRouter `usage.cost` (USD, provider-reported); None when absent or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = ["parse_usage"]


def reasoning_of(delta: Any) -> list[str]:
    """Thinking text in one LiteLLM stream delta: `reasoning_content` (OpenAI-style, DeepSeek,
    OpenRouter) or Anthropic `thinking_blocks[].thinking`; `[]` when the chunk has none."""
    if delta is None:
        return []
    text = getattr(delta, "reasoning_content", None)
    if isinstance(text, str) and text:
        return [text]
    out: list[str] = []
    for block in getattr(delta, "thinking_blocks", None) or []:
        thought = block.get("thinking") if isinstance(block, dict) else getattr(block, "thinking", None)
        if isinstance(thought, str) and thought:
            out.append(thought)
    return out
