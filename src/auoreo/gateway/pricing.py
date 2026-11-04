"""Estimated call price from a LiteLLM cost function (M7 W1a, step 1).

`prompt_tokens` is the TOTAL prompt; `cached` (read from the provider cache) and `written`
(written to it) are subsets of it. LiteLLM prices the split when given
`cache_read_input_tokens` and `cache_creation_input_tokens`; without them every cached token
is charged at the full input rate. The cost function is injected so `litellm` stays imported
only by `gateway.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def price(
    cost_per_token: Callable[..., tuple[float, float]],
    candidates: Iterable[str],
    prompt: int,
    completion: int,
    *,
    cached: int,
    written: int,
    cache_aware: bool,
) -> float | None:
    """USD for one call, or None when no candidate name has pricing."""
    extra: dict[str, Any] = {}
    if cache_aware and cached:
        extra["cache_read_input_tokens"] = cached
    if cache_aware and written:
        extra["cache_creation_input_tokens"] = written
    for name in candidates:
        try:
            p, c = cost_per_token(model=name, prompt_tokens=prompt, completion_tokens=completion, **extra)
        except Exception:
            continue
        if p or c:
            return float(p + c)
    return None
