"""`chunk_strategy: late` = contextual chunk embedding (memory/contextual.py) + span provenance."""

from __future__ import annotations

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway
from auoreo.memory.chunking import chunk_text
from auoreo.memory.contextual import WINDOW_CHARS, batches, context_window, contextual_inputs

DOC = "\n\n".join(
    [
        "Operations handbook.",
        "On-call rotates weekly on Mondays at 09:00 and the outgoing engineer writes a handover note.",
        "Database snapshots run nightly at 02:00 UTC and are kept for 35 days in a separate account.",
        "Secrets rotate every ninety days through the vault; never paste them into chat or tickets.",
        "Capacity reviews happen at the start of each quarter with finance in the room.",
    ]
)


def count(text: str) -> int:
    return max(1, len(text) // 4)


@pytest.fixture
def embeds(gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    real = gateway.embed

    async def spy(model: str, texts: list[str], dims: int) -> list[list[float]]:
        calls.append(list(texts))
        return await real(model, texts, dims)

    monkeypatch.setattr(gateway, "embed", spy)
    return calls


def test_chunk_spans_point_into_the_source() -> None:
    chunks = chunk_text(DOC, 30, 0, count)
    assert len(chunks) > 2
    prev = -1
    for c in chunks:
        assert 0 <= c.span_start < c.span_end <= len(DOC) and c.span_start > prev
        assert DOC[c.span_start : c.span_end].split() == c.text.split()
        prev = c.span_start


def test_context_window_and_batches() -> None:
    assert context_window(DOC, 10, 20, 12_000) == DOC  # small document: all of it
    big = "x" * 50_000
    win = context_window(big, 49_990, 50_000, 12_000)
    assert len(win) == WINDOW_CHARS
    assert len(context_window(big, 0, 10, 3000)) == 3000  # never above late_max_chars
    assert batches(["a" * 6, "b" * 6, "c" * 6], max_chars=12) == [["a" * 6, "b" * 6], ["c" * 6]]
    chunks = chunk_text(DOC, 30, 0, count)
    inputs = contextual_inputs(DOC, chunks, 12_000)
    assert all(i.startswith(c.text) and i.endswith(DOC) for i, c in zip(inputs, chunks, strict=True))


async def test_early_is_default_and_exposes_provenance(services: Services, embeds: list[list[str]]) -> None:
    mem = services.memory
    assert mem is not None and services.settings.memory.chunk_strategy == "early"
    services.settings.memory.chunk_tokens = 30
    services.settings.memory.chunk_overlap = 0
    added = await mem.ingest(DOC, "handbook.md")
    assert added > 2
    assert embeds[-1] == [c.text for c in chunk_text(DOC, 30, 0, count)]  # each chunk alone
    hits = await mem.search("how long are snapshots kept", kinds=("chunk",))
    top = hits[0]
    assert "35 days" in top.text and top.chunk_strategy == "early"
    assert top.span_start is not None and top.span_end is not None
    assert "35 days" in DOC[top.span_start : top.span_end]
    facts = await mem.search("snapshots")
    assert all(h.span_start is None for h in facts if h.kind == "fact")


async def test_late_embeds_spans_with_document_context_in_one_call(
    services: Services, embeds: list[list[str]]
) -> None:
    mem = services.memory
    assert mem is not None
    cfg = services.settings.memory
    cfg.chunk_strategy, cfg.chunk_tokens, cfg.chunk_overlap = "late", 30, 0
    embeds.clear()
    added = await mem.ingest(DOC, "handbook.md", doc_id="hb")
    assert len(embeds) == 1 and len(embeds[0]) == added > 2  # one batched call per document
    assert all("[document context]" in t and t.endswith(DOC) for t in embeds[0])
    rows = mem.store.db.execute(
        "SELECT chunk_strategy, span_start, span_end, doc_id FROM chunks ORDER BY id"
    ).fetchall()
    assert {r[0] for r in rows} == {"late"} and {r[3] for r in rows} == {"hb"}
    assert all(r[1] is not None and r[2] > r[1] for r in rows)
    hits = await mem.search("how long are snapshots kept", kinds=("chunk",))
    assert hits and "35 days" in hits[0].text and hits[0].chunk_strategy == "late"


async def test_late_large_document_uses_windows(services: Services, embeds: list[list[str]]) -> None:
    mem = services.memory
    assert mem is not None
    cfg = services.settings.memory
    cfg.chunk_strategy, cfg.late_max_chars = "late", 2000
    text = "\n\n".join(f"Paragraph {i} talks about topic {i} in some detail. " * 8 for i in range(40))
    embeds.clear()
    await mem.ingest(text, "big.md")
    assert embeds and all(len(t.split("[document context]\n", 1)[1]) <= 2000 for b in embeds for t in b)


async def test_late_embedding_failure_still_stores_chunks(
    services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = services.memory
    assert mem is not None
    services.settings.memory.chunk_strategy = "late"

    async def broken(model: str, texts: list[str], dims: int) -> list[list[float]]:
        raise RuntimeError("no embeddings")

    monkeypatch.setattr(gateway, "embed", broken)
    assert await mem.ingest(DOC, "handbook.md") >= 1
    hits = await mem.search("snapshots kept days", kinds=("chunk",))
    assert hits and hits[0].chunk_strategy == "late"  # FTS-only, provenance intact
