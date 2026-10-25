"""HyDE (memory/hyde.py): aux call on zero FTS hits, vector arm only, never injected."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import LLMRequest, StreamItem
from auoreo.memory import hyde

REPO = Path(__file__).resolve().parents[2]
NO_FTS = "favorite hue?"


def _hyde_requests(gw: FakeGateway) -> list[LLMRequest]:
    return [r for r in gw.requests if r.metadata.get("purpose") == "hyde"]


@pytest.fixture
def embeds(gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    real = gateway.embed

    async def spy(model: str, texts: list[str], dims: int) -> list[list[float]]:
        calls.append(list(texts))
        return await real(model, texts, dims)

    monkeypatch.setattr(gateway, "embed", spy)
    return calls


async def _seed(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await mem.add_fact("The user likes teal more than any other colour.")
    await mem.add_fact("Coffee in the office kitchen is free.")


async def test_off_by_default_no_aux_call(services: Services, gateway: FakeGateway) -> None:
    await _seed(services)
    assert services.settings.memory.recall_hyde is False
    assert services.memory is not None
    await services.memory.search(NO_FTS)
    assert _hyde_requests(gateway) == []


async def test_zero_fts_hits_trigger_hyde_for_the_vector_arm_only(
    services: Services, gateway: FakeGateway, embeds: list[list[str]]
) -> None:
    mem = services.memory
    assert mem is not None
    await _seed(services)
    services.settings.memory.recall_hyde = True
    embeds.clear()
    hits = await mem.search(NO_FTS)
    [req] = _hyde_requests(gateway)
    assert req.temperature == 0 and req.max_tokens == 128 and req.model == "small"
    assert req.tool_choice == "none" and req.messages[-1]["content"] == NO_FTS
    [[vector_text]] = embeds
    doc = vector_text.split("\n", 1)[1]
    assert vector_text.startswith(NO_FTS + "\n") and doc
    assert all(doc not in h.text for h in hits)  # never injected
    block = await mem.context_block(NO_FTS)
    assert doc not in block
    stats = await mem.stats()
    assert stats["hyde_used"] == 2 and stats["hyde_failed"] == 0


async def test_fts_hits_or_long_queries_or_no_aux_skip_hyde(services: Services, gateway: FakeGateway) -> None:
    mem = services.memory
    assert mem is not None
    await _seed(services)
    services.settings.memory.recall_hyde = True
    await mem.search("which colour does the user like")  # FTS hits
    await mem.search("favorite hue " * 20)  # ≥ 160 chars
    await mem.search(NO_FTS, allow_aux=False)  # unattended / subagent callers
    await mem.context_block(NO_FTS, allow_aux=False)
    assert _hyde_requests(gateway) == []
    assert (await mem.stats())["hyde_used"] == 0


async def test_gateway_failure_falls_back_silently(
    services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch, embeds: list[list[str]]
) -> None:
    mem = services.memory
    assert mem is not None
    await _seed(services)
    services.settings.memory.recall_hyde = True
    real = gateway.stream

    async def broken(request: LLMRequest) -> AsyncIterator[StreamItem]:
        if request.metadata.get("purpose") == "hyde":
            raise GatewayError("SERVER", "boom")
        async for item in real(request):
            yield item

    monkeypatch.setattr(gateway, "stream", broken)
    embeds.clear()
    hits = await mem.search(NO_FTS)
    assert embeds == [[NO_FTS]] and hits  # plain query embedding, results still come back
    stats = await mem.stats()
    assert stats["hyde_used"] == 0 and stats["hyde_failed"] == 1


async def test_timeout_falls_back(
    services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hyde, "TIMEOUT_S", 0.05)

    async def slow(request: LLMRequest) -> AsyncIterator[StreamItem]:
        await asyncio.sleep(5)
        yield  # type: ignore[misc]

    monkeypatch.setattr(gateway, "stream", slow)
    assert await hyde.generate(services, NO_FTS, 64) is None


def test_helpers() -> None:
    assert hyde.triggered("short", 0) and not hyde.triggered("short", 1)
    assert not hyde.triggered("x" * 160, 0)
    assert hyde.clip_sentences("One. Two!  Three? Four.") == "One. Two!"


async def test_fake_answers_hyde_deterministically(gateway: FakeGateway) -> None:
    req = LLMRequest(
        model="small", messages=({"role": "user", "content": "Favorite hue?"},), metadata={"purpose": "hyde"}
    )
    texts: list[Any] = []
    for _ in range(2):
        texts.append("".join(getattr(i, "text", "") for i in [x async for x in gateway.stream(req)]))
    assert texts[0] == texts[1] == "Favorite hue (hypothetical)."
    assert not gateway.script  # never eats the main script


def test_eval_hyde_runs_on_zero_fts_queries() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import eval_memory
    finally:
        sys.path.remove(str(REPO / "scripts"))
    rows = eval_memory.load_golden(eval_memory.GOLDEN)
    paraphrase = [r for r in rows if r.get("subset") == "paraphrase"]
    res = asyncio.run(eval_memory.run_eval(only=["none"], hyde=True))["none"]
    assert paraphrase and res["hyde_used"] >= len(paraphrase)
    assert res["subsets"]["paraphrase"]["n"] == len(paraphrase)
