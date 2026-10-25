"""Memory retrieval eval (spec §24.3 / §22 spike 2): recall@10, nDCG@10, p95 per reranker.

Loads `tests/fixtures/memory_golden.jsonl` (query → expected fact + bridge facts +
distractors, or query → document + answer substring), ingests every fact and document into
a temporary `memory.db` using the fake gateway's hashed embeddings, then runs each query
through `MemoryService.search` once per rerank backend: `none`, `fake`, plus every other
`backends/rerank/*` whose `check()` passes (e.g. `cross_encoder` with a cached model,
`cohere` with COHERE_API_KEY).

Rows carry an optional `subset`: `entity_hop` (the answer is one entity hop away from the
query, through a `bridge` fact), `global` (summary queries; a community summary that
contains the expected fact counts as a hit), `paraphrase` (no FTS hit at all: the HyDE
trigger) and `doc` (chunk retrieval; a hit is a chunk containing `answer`). Overall
recall/nDCG cover the fact rows; every subset is also reported on its own.

Opt-in retrieval options (spec §25.8): `--graph` (GraphRAG-lite: communities are built
after ingest), `--hyde`, `--chunk-strategy early|late`.

    uv run python scripts/eval_memory.py [--golden PATH] [--backend NAME ...] [--json]
        [--model HF_OR_PROVIDER_ID] [--download] [--timeout SECONDS]
        [--graph] [--hyde] [--chunk-strategy early|late]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "fixtures" / "memory_golden.jsonl"
K = 10
EVAL_CHUNK_TOKENS = 64
"""Small chunks so the `doc` rows split into several spans."""
COST_PER_1K = {"cohere": 2.0}
"""Approximate USD per 1k rerank queries (Cohere bills per search unit); local = 0."""


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("query") and (r.get("fact") or (r.get("doc") and r.get("answer")))]


def load_golden(path: Path) -> list[dict[str, Any]]:
    """Fact rows only (query → expected fact)."""
    return [r for r in load_rows(path) if r.get("fact")]


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def _rank(row: dict[str, Any], hits: list[Any], ids: dict[str, int]) -> int | None:
    """0-based rank of the expected item in `hits`, or None when missing."""
    if row.get("doc"):
        chunks = [h for h in hits if h.kind == "chunk"][:K]
        return next((i for i, h in enumerate(chunks) if row["answer"] in h.text), None)
    ranked = [h.id for h in hits if h.kind == "fact"][:K]
    want = ids[row["fact"]]
    if want in ranked:
        return ranked.index(want)
    themes = [h for h in hits if h.kind == "community"]
    if any(row["fact"] in h.text for h in themes):
        return len(ranked)  # appended after the RRF top-N
    return None


def _score(rows: list[dict[str, Any]], ranks: list[int | None]) -> dict[str, float]:
    n = len(rows) or 1
    found = [r for r in ranks if r is not None]
    return {
        "recall@10": round(len(found) / n, 3),
        "ndcg@10": round(sum(1.0 / math.log2(r + 2) for r in found) / n, 3),
    }


async def _ingest(mem: Any, rows: list[dict[str, Any]]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for i, r in enumerate(rows):
        if r.get("doc"):
            await mem.ingest(r["doc"], f"golden:{i}", doc_id=f"golden-{i}")
            continue
        for text in [r["fact"], *r.get("bridge", []), *r.get("distractors", [])]:
            fid, _ = await mem.add_fact(text, source="eval")
            ids[text] = fid
    return ids


async def run_eval(
    golden: Path = GOLDEN,
    only: list[str] | None = None,
    *,
    graph: bool = False,
    hyde: bool = False,
    chunk_strategy: str = "early",
    **overrides: Any,
) -> dict[str, dict[str, Any]]:
    from auoreo.core.services import Services
    from auoreo.core.settings import MemorySettings, Settings
    from auoreo.gateway.fake import FakeGateway

    rows = load_rows(golden)
    fact_rows = [r for r in rows if r.get("fact")]
    memory = MemorySettings(
        graph_enabled=graph,
        recall_hyde=hyde,
        chunk_strategy=chunk_strategy,  # type: ignore[arg-type]
        chunk_tokens=EVAL_CHUNK_TOKENS,
        chunk_overlap=8,
    )
    with tempfile.TemporaryDirectory(prefix="auoreo-eval-") as tmp:
        home = Path(tmp)
        settings = Settings(home=home, gateway="fake", rerank_top_n=K, memory=memory, **overrides)
        svc = await Services.create(
            settings, cwd=REPO, gateway=FakeGateway(), with_mcp=False, with_extensions=False
        )
        try:
            mem = svc.memory
            assert mem is not None
            ids = await _ingest(mem, rows)
            if graph:
                await mem.build_graph()
            results: dict[str, dict[str, Any]] = {}
            found = svc.harness.backends_for("rerank")
            for name, bdef in sorted(found.items(), key=lambda kv: (kv[0] != "none", kv[0])):
                if only and name not in only:
                    continue
                backend = bdef.load()(settings)
                status = await backend.check()
                if not status.ok:
                    results[name] = {"skipped": status.detail}
                    continue
                if (warm := getattr(backend, "warm", None)) is not None:
                    await warm()  # load model weights outside the timed loop
                mem.reranker = backend
                lat: list[float] = []
                ranks: list[int | None] = []
                skipped_before = mem.rerank_stats.skipped
                before = dict(mem.recall_stats.as_dict())
                for r in rows:
                    kinds = ("chunk",) if r.get("doc") else ("fact", "chunk")
                    started = time.perf_counter()
                    hits = await mem.search(r["query"], limit=K, kinds=kinds)
                    lat.append((time.perf_counter() - started) * 1000)
                    ranks.append(_rank(r, hits, ids))
                by_subset: dict[str, tuple[list[dict[str, Any]], list[int | None]]] = {}
                for r, rank in zip(rows, ranks, strict=True):
                    sub = by_subset.setdefault(r.get("subset", "base"), ([], []))
                    sub[0].append(r)
                    sub[1].append(rank)
                fact_ranks = [rk for r, rk in zip(rows, ranks, strict=True) if r.get("fact")]
                after = mem.recall_stats.as_dict()
                results[name] = {
                    **_score(fact_rows, fact_ranks),
                    "p95_ms": round(p95(lat), 2),
                    "rerank_skipped": mem.rerank_stats.skipped - skipped_before,
                    "cost_per_1k_usd": COST_PER_1K.get(name, 0.0),
                    "detail": status.detail,
                    "subsets": {k: {"n": len(v[0]), **_score(*v)} for k, v in sorted(by_subset.items())},
                    "hyde_used": int(str(after["hyde_used"])) - int(str(before["hyde_used"])),
                    "graph_hit": int(str(after["graph_hit"])) - int(str(before["graph_hit"])),
                }
            return results
        finally:
            await svc.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    ap.add_argument("--backend", action="append", help="only these rerank backends (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model", help="settings.rerank_model for this run")
    ap.add_argument("--download", action="store_true", help="allow model downloads")
    ap.add_argument("--timeout", type=float, help="settings.rerank_timeout_s for this run")
    ap.add_argument("--graph", action="store_true", help="GraphRAG-lite (memory.graph_enabled)")
    ap.add_argument("--hyde", action="store_true", help="HyDE on zero-FTS-hit queries (memory.recall_hyde)")
    ap.add_argument("--chunk-strategy", choices=("early", "late"), default="early")
    args = ap.parse_args(argv)
    overrides: dict[str, Any] = {"rerank_download_models": args.download}
    if args.model:
        overrides["rerank_model"] = args.model
    if args.timeout:
        overrides["rerank_timeout_s"] = args.timeout
    options = {"graph": args.graph, "hyde": args.hyde, "chunk_strategy": args.chunk_strategy}
    results = asyncio.run(run_eval(args.golden, args.backend, **options, **overrides))
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    n = len(load_golden(args.golden))
    flags = ", ".join(f"{k}={v}" for k, v in options.items())
    print(f"memory eval: {n} fact queries, k={K}, fake hashed embeddings ({flags})")
    print(f"{'backend':<14} {'recall@10':>9} {'nDCG@10':>8} {'p95 ms':>8} {'skipped':>7} {'$/1k':>6}")
    for name, r in results.items():
        if "skipped" in r:
            print(f"{name:<14} {'—':>9} {'—':>8} {'—':>8} {'—':>7} {'—':>6}  unavailable: {r['skipped']}")
            continue
        print(
            f"{name:<14} {r['recall@10']:>9.3f} {r['ndcg@10']:>8.3f} {r['p95_ms']:>8.2f} "
            f"{r['rerank_skipped']:>7} {r['cost_per_1k_usd']:>6.2f}"
        )
        subsets = "  ".join(f"{k}({v['n']}) {v['recall@10']:.3f}" for k, v in r["subsets"].items())
        print(f"{'':<14} recall@10 by subset: {subsets}")
        print(f"{'':<14} hyde_used={r['hyde_used']} graph_hit={r['graph_hit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
