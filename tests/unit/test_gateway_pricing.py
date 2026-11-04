"""Cache-aware pricing (M7 W1a step 1): cached reads are cheap, cache writes cost extra.

`Gateway.price` used to call `litellm.cost_per_token(prompt, completion)` and so charged every
cached token at the full input rate. With `m7.token.cached_pricing` the cache split is passed
through; `prompt_tokens` stays the TOTAL prompt (cached and written tokens are subsets of it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.settings import Settings
from auoreo.gateway import pricing
from auoreo.gateway.fake import FakeGateway, Scripted

CLAUDE = "anthropic/claude-sonnet-4-5"


def _gateway(home: Path, *, cached_pricing: bool):  # type: ignore[no-untyped-def]
    from auoreo.gateway.gateway import LiteLLMGateway

    st = Settings(
        home=home, models={"main": {"models": [CLAUDE]}}, m7={"token": {"cached_pricing": cached_pricing}}
    )
    return LiteLLMGateway(st, lambda _n: None)


def test_cache_split_changes_the_price_in_the_expected_order(home: Path) -> None:
    gw = _gateway(home, cached_pricing=True)
    plain = gw.price(CLAUDE, 1000, 100)
    cached = gw.price(CLAUDE, 1000, 100, cached_tokens=1000)
    written = gw.price(CLAUDE, 1000, 100, cache_write_tokens=1000)
    assert plain is not None and cached is not None and written is not None
    assert cached < plain < written


def test_flag_off_ignores_the_cache_split(home: Path) -> None:
    gw = _gateway(home, cached_pricing=False)
    assert gw.price(CLAUDE, 1000, 100, cached_tokens=1000) == gw.price(CLAUDE, 1000, 100)


def test_zero_cache_tokens_do_not_reach_the_cost_function() -> None:
    seen: list[dict[str, object]] = []

    def fake_cost(**kw: object) -> tuple[float, float]:
        seen.append(kw)
        return (0.002, 0.001)

    total = pricing.price(fake_cost, ["m"], 10, 5, cached=0, written=0, cache_aware=True)
    assert total == pytest.approx(0.003) and "cache_read_input_tokens" not in seen[0]
    pricing.price(fake_cost, ["m"], 10, 5, cached=4, written=2, cache_aware=True)
    assert seen[1]["cache_read_input_tokens"] == 4 and seen[1]["cache_creation_input_tokens"] == 2
    pricing.price(fake_cost, ["m"], 10, 5, cached=4, written=2, cache_aware=False)
    assert "cache_read_input_tokens" not in seen[2]


def test_unknown_model_prices_to_none_and_failures_fall_through() -> None:
    def boom(**kw: object) -> tuple[float, float]:
        raise ValueError("no pricing")

    assert pricing.price(boom, ["a", "b"], 10, 5, cached=0, written=0, cache_aware=True) is None


def test_fake_gateway_price_follows_the_cache_split() -> None:
    gw = FakeGateway()
    assert gw.price("m", 1000, 100) == 0.0  # unchanged default: no cache split, no cost
    cached = gw.price("m", 1000, 100, cached_tokens=1000)
    written = gw.price("m", 1000, 100, cache_write_tokens=1000)
    assert cached is not None and written is not None and cached < written
    assert Scripted(cached_tokens=3, cache_write_tokens=4).cached_tokens == 3
