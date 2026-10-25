"""Rerank backends + the fail-open rerank step of the memory pipeline (spec §24.3)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from auoreo.backends.rerank import Ranked, RerankBackend
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.memory.rerank import RerankStats
from auoreo.memory.rerank import apply as rerank_hits

REPO = Path(__file__).resolve().parents[2]
FACTS = [
    "The deploy pipeline uses GitHub Actions and Argo CD",
    "Lunch on Friday is usually pizza with the deploy team",
    "The user prefers the helix editor for deploy scripts",
    "Deploy freezes run from mid December into January",
]


def _load(services: Services, name: str) -> Any:
    return services.harness.backends_for("rerank")[name].load()(services.settings)


class Reverse(RerankBackend):
    name = "reverse"

    def __init__(self, settings: Any = None) -> None:
        super().__init__(settings)
        self.calls = 0

    async def check(self) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        self.calls += 1
        n = len(documents)
        return [Ranked(n - 1 - i, float(i)) for i in range(n)][:top_n]


class Boom(Reverse):
    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        self.calls += 1
        raise RuntimeError("model exploded")


class Slow(Reverse):
    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        self.calls += 1
        await asyncio.sleep(5)
        return []


async def _seed(services: Services) -> list[int]:
    mem = services.memory
    assert mem is not None
    return [(await mem.add_fact(f))[0] for f in FACTS]


async def _baseline(services: Services) -> list[int]:
    mem = services.memory
    assert mem is not None
    mem.reranker = _load(services, "none")
    return [h.id for h in await mem.search("deploy pipeline", limit=8)]


# ------------------------------------------------------------- backends
async def test_fake_backend_reorders_by_token_overlap(services: Services) -> None:
    fake = _load(services, "fake")
    assert fake.enabled and (await fake.check()).ok
    docs = ["pizza on friday", "argo cd deploy pipeline", "deploy freeze"]
    ranked = await fake.rerank("deploy pipeline with argo", docs, 2)
    assert [r.index for r in ranked] == [1, 2]
    assert ranked[0].score > ranked[1].score


async def test_fake_reranker_reorders_the_memory_pipeline(services: Services) -> None:
    await _seed(services)
    mem = services.memory
    assert mem is not None
    mem.reranker = _load(services, "fake")
    hits = await mem.search("argo actions pipeline", limit=8)
    assert hits[0].text == FACTS[0]
    stats = await mem.stats()
    assert stats["rerank_calls"] == 1 and stats["rerank_skipped"] == 0
    assert stats["rerank_backend"] == "fake" and isinstance(stats["rerank_ms"], float)


async def test_rerank_keeps_min_limit_top_n_in_backend_order(services: Services) -> None:
    await _seed(services)
    base = await _baseline(services)
    mem = services.memory
    assert mem is not None
    services.settings.rerank_top_n = 2
    mem.reranker = Reverse()
    got = [h.id for h in await mem.search("deploy pipeline", limit=8)]
    assert got == list(reversed(base))[:2]


async def test_failing_backend_falls_back_to_rrf_and_counts(
    services: Services, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed(services)
    base = await _baseline(services)
    mem = services.memory
    assert mem is not None
    mem.reranker = boom = Boom()
    with caplog.at_level("INFO", logger="auoreo.memory.rerank"):
        got = [h.id for h in await mem.search("deploy pipeline", limit=8)]
    assert got == base and boom.calls == 1
    assert (await mem.stats())["rerank_skipped"] == 1
    assert "rerank skipped" in caplog.text and "model exploded" in caplog.text


async def test_timeout_falls_back_to_rrf(services: Services) -> None:
    await _seed(services)
    base = await _baseline(services)
    mem = services.memory
    assert mem is not None
    services.settings.rerank_timeout_s = 0.05
    mem.reranker = Slow()
    got = [h.id for h in await mem.search("deploy pipeline", limit=8)]
    assert got == base
    stats = await mem.stats()
    assert stats["rerank_skipped"] == 1 and stats["rerank_ms"] < 2000


async def test_none_backend_is_never_called(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed(services)
    none = services.backend("rerank")
    assert none.name == "none" and none.enabled is False

    async def explode(*_a: Any, **_k: Any) -> list[Ranked]:
        raise AssertionError("the none backend must not be called")

    monkeypatch.setattr(none, "rerank", explode)
    mem = services.memory
    assert mem is not None
    assert await mem.search("deploy pipeline", limit=8)
    stats = await mem.stats()
    assert stats["rerank_calls"] == 0 and stats["rerank_backend"] == "none"


async def test_bad_indices_fail_open() -> None:
    class Bad(Reverse):
        async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
            return [Ranked(99, 1.0)]

    class Doc:
        def __init__(self, text: str) -> None:
            self.text = text

    docs = [Doc("a"), Doc("b"), Doc("c")]
    stats = RerankStats()
    out = await rerank_hits(Bad(), "q", docs, 2, top_n=8, timeout_s=1, stats=stats)
    assert out == docs[:2] and stats.skipped == 1


async def test_cohere_request_shape(
    services: Services, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "co-test-key")
    cohere = _load(services, "cohere")
    assert (await cohere.check()).ok and cohere.model == "rerank-v3.5"
    httpx_mock.add_response(
        method="POST",
        url="https://api.cohere.com/v2/rerank",
        json={
            "id": "x",
            "results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}],
        },
    )
    ranked = await cohere.rerank("deploy", ["a", "b", "c"], 2)
    assert ranked == [Ranked(2, 0.9), Ranked(0, 0.2)]
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer co-test-key"
    assert json.loads(req.content) == {
        "model": "rerank-v3.5",
        "query": "deploy",
        "documents": ["a", "b", "c"],
        "top_n": 2,
    }


async def test_cohere_errors_and_missing_key_fail_open(
    services: Services, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    services.settings.rerank_model = "rerank-v4.0-fast"
    cohere = _load(services, "cohere")
    status = await cohere.check()
    assert not status.ok and "COHERE_API_KEY" in status.detail and cohere.model == "rerank-v4.0-fast"
    with pytest.raises(RuntimeError, match="COHERE_API_KEY"):
        await cohere.rerank("q", ["a"], 1)
    monkeypatch.setenv("COHERE_API_KEY", "co-test-key")
    httpx_mock.add_response(method="POST", status_code=429, json={"message": "slow down"})
    with pytest.raises(RuntimeError, match="429") as exc:
        await cohere.rerank("q", ["a", "b"], 1)
    assert "co-test-key" not in str(exc.value)


async def test_cross_encoder_check_is_honest(
    services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hf"))
    ce = _load(services, "cross_encoder")
    assert ce.model_name == "cross-encoder/ms-marco-MiniLM-L6-v2"
    status = await ce.check()
    assert not status.ok
    if importlib.util.find_spec("sentence_transformers") is None:
        assert "not installed" in status.detail and "rerank-local" in (status.remedy or "")
    else:
        assert "not downloaded" in status.detail and "rerank_download_models" in (status.remedy or "")
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "sentence_transformers" else real_find_spec(name, *a),
    )
    missing = await ce.check()
    assert not missing.ok and "not installed" in missing.detail
    services.settings.rerank_download_models = True
    monkeypatch.setattr(importlib.util, "find_spec", real_find_spec)
    if real_find_spec("sentence_transformers") is not None:
        assert (await ce.check()).ok  # downloads allowed: reported as "downloads on first use"


# ------------------------------------------------------------------ eval
def test_eval_script_runs_and_fake_does_not_lower_recall(home: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import eval_memory
    finally:
        sys.path.remove(str(REPO / "scripts"))
    results = asyncio.run(eval_memory.run_eval(only=["none", "fake"]))
    assert results["fake"]["recall@10"] >= results["none"]["recall@10"] > 0
    assert results["fake"]["rerank_skipped"] == 0
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "eval_memory.py"), "--backend", "none", "--backend", "fake"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout
    assert "recall@10" in out and "nDCG@10" in out and "fake" in out


def test_settings_defaults() -> None:
    s = Settings()
    assert (s.rerank_model, s.rerank_download_models, s.rerank_timeout_s, s.rerank_top_n) == (
        "cross-encoder/ms-marco-MiniLM-L6-v2",
        False,
        2.0,
        8,
    )
    assert s.otel_mode == "off" and s.otel_capture_content is False
