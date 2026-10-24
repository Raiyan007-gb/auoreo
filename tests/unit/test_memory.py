from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.memory import store as store_mod
from auoreo.memory.chunking import chunk_text
from auoreo.memory.files import read_capped, truncate
from auoreo.memory.service import rrf
from auoreo.memory.store import MemoryStore, fts_query


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("what is the deploy command?", '"deploy" OR "command"'),
        ('he said "rm" AND (x', '"he" OR "said" OR "rm"'),
        ("a an the ?", None),
        ("NEAR(foo bar) OR *", '"near" OR "foo" OR "bar"'),
    ],
)
def test_fts_query_sanitizes(raw: str, expected: str | None) -> None:
    assert fts_query(raw) == expected


def test_rrf_math() -> None:
    scores = rrf([[("f", 1), ("f", 2)], [("f", 2), ("f", 3)]], k=60)
    assert scores[("f", 2)] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[("f", 2)] > scores[("f", 1)] > scores[("f", 3)]


async def test_fact_dedup_and_hybrid_search(services: Services) -> None:
    mem = services.memory
    assert mem is not None and mem.store.vec_enabled
    fid, created = await mem.add_fact("The user deploys with `make ship` on Fridays")
    fid2, created2 = await mem.add_fact("The user deploys with `make ship` on Fridays")
    assert created and not created2 and fid == fid2
    await mem.add_fact("Favourite editor is helix")
    hits = await mem.search("how does the user deploy?")
    assert hits and hits[0].id == fid and hits[0].citation == f"fact:{fid}"
    assert await mem.search("the a an") == []  # trivial query → no SQL, no results


async def test_vec_unavailable_falls_back_to_fts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MemoryStore, "_load_vec", lambda self: False)
    s = MemoryStore(tmp_path / "m.db", dims=8)
    s.add_fact("python packaging uses uv", "project", 0.5, [0.1] * 8, "m")
    assert s.fts("fact", "packaging", 5) and s.knn("fact", [0.1] * 8, 5) == []
    s.close()


async def test_low_trust_facts_are_filtered(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fid, _ = await mem.add_fact("untrusted rumour about kubernetes")
    mem.store.bump_trust(fid, -0.4)
    assert await mem.search("kubernetes rumour") == []


async def test_ingest_and_cite_chunks(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    text = "# Guide\n\nInstall with uv sync.\n\nRun tests with pytest -q.\n" * 3
    added = await mem.ingest(text, "/docs/guide.md")
    assert added >= 1
    hits = await mem.search("how to run tests pytest", kinds=("chunk",))
    assert hits and hits[0].citation.startswith("/docs/guide.md:")


async def test_context_block_contains_files_and_recall(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await mem.add_fact("Prefers concise answers with code references")
    block = await mem.context_block("give me concise answers please")
    assert "# Memory index" in block and "<memory-context>" in block and "🧠 memory" in block


def test_truncate_names_the_cap() -> None:
    out = truncate("line\n" * 300, max_lines=200, label="MEMORY.md")
    assert out.count("\n") <= 201 and "200 lines" in out and "WARNING" in out
    out = truncate("x" * 3000, max_chars=100, label="USER.md")
    assert "100 chars" in out


def test_read_capped_copies_templates(home: Path) -> None:
    assert "Memory index" in read_capped(home, "MEMORY.md", 2200)
    assert (home / "memory" / "USER.md").is_file()


def test_chunking_respects_budget_and_overlap() -> None:
    def count(t: str) -> int:
        return len(t.split())

    text = "\n\n".join(" ".join(f"w{p}_{i}" for i in range(40)) for p in range(10))
    chunks = chunk_text(text, max_tokens=100, overlap=10, count=count)
    assert len(chunks) > 1
    assert all(c.tokens <= 100 + 12 for c in chunks)
    assert chunks[1].text.startswith("[continued]")


def test_stopwords_are_lowercase() -> None:
    assert all(w == w.lower() for w in store_mod.STOPWORDS)
