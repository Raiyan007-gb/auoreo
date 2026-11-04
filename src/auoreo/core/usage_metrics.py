"""Cache token counters for the usage ledger (M7 W1a).

`tokens_total{type="cached"|"cache_write"}` are SUBSETS of `type="prompt"`: a dashboard shows the
hit ratio as `cached / prompt`. Kept out of `observability.py`, which is at the line cap.
"""

from __future__ import annotations

from auoreo.core import observability as obs


def record_cache_usage(model: str, cached_tokens: int, cache_write_tokens: int) -> None:
    if cached_tokens:
        obs.count("tokens_total", cached_tokens, model=model, type="cached")
    if cache_write_tokens:
        obs.count("tokens_total", cache_write_tokens, model=model, type="cache_write")
