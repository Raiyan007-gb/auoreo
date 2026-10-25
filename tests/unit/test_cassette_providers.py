"""G5c: cassette-backed provider HTTP tests (spec §20.4 "recorded" tier).

Replays `tests/fixtures/cassettes/test_cassette_providers/*.yaml` through the real Cohere
rerank backend (`backends/rerank/cohere`) — real httpx client, real request shape — with
record mode `none`, so no network and no key are needed. Re-record explicitly (see
`tests/cassettes.py`); the key in the env is filtered out of the cassette.
"""

from __future__ import annotations

from typing import Any

import pytest

from auoreo.backends.rerank import Ranked
from auoreo.core.services import Services

DOCS = [
    "The deploy pipeline uses GitHub Actions and Argo CD",
    "Lunch on Friday is usually pizza with the deploy team",
    "Deploy freezes run from mid December into January",
]


def _cohere(services: Services) -> Any:
    return services.harness.backends_for("rerank")["cohere"].load()(services.settings)


@pytest.fixture
def cohere_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    if not os.environ.get("COHERE_API_KEY"):  # replay: any value; the cassette holds REDACTED
        monkeypatch.setenv("COHERE_API_KEY", "co-replay-placeholder")


@pytest.mark.vcr
async def test_cohere_rerank_replays_cassette(services: Services, cohere_key: None) -> None:
    ranked = await _cohere(services).rerank("how do we deploy?", DOCS, 2)
    assert ranked == [Ranked(0, 0.87421), Ranked(2, 0.31877)]


@pytest.mark.vcr
async def test_cohere_rerank_error_is_typed_and_key_free(services: Services, cohere_key: None) -> None:
    services.settings.rerank_model = "rerank-v9-missing"
    backend = _cohere(services)
    with pytest.raises(Exception, match=r"HTTP 404: model 'rerank-v9-missing' not found") as info:
        await backend.rerank("q", ["a"], 1)
    assert type(info.value).__name__ == "RerankUnavailable"
    assert "co-replay-placeholder" not in str(info.value)
