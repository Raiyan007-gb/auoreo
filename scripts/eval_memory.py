"""Memory retrieval eval (spec §24.3 / §22 spike 2): recall@10, nDCG@10, p95 per reranker.

Loads `tests/fixtures/memory_golden.jsonl` (query → expected fact + distractors), ingests
every fact into a temporary `memory.db` using the fake gateway's hashed embeddings, then
runs each query through `MemoryService.search` once per rerank backend: `none`, `fake`,
plus every other `backends/rerank/*` whose `check()` passes (e.g. `cross_encoder` with a
cached model, `cohere` with COHERE_API_KEY).

    uv run python scripts/eval_memory.py [--golden PATH] [--backend NAME ...] [--json]
        [--model HF_OR_PROVIDER_ID] [--download] [--timeout SECONDS]
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
COST_PER_1K = {"cohere": 2.0}
"""Approximate USD per 1k rerank queries (Cohere bills per search unit); local = 0."""


def load_golden(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("query") and r.get("fact")]


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


async def run_eval(
    golden: Path = GOLDEN, only: list[str] | None = None, **overrides: Any
) -> dict[str, dict[str, Any]]:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings
    from auoreo.gateway.fake import FakeGateway

    rows = load_golden(golden)
    with tempfile.TemporaryDirectory(prefix="auoreo-eval-") as tmp:
        home = Path(tmp)
        settings = Settings(home=home, gateway="fake", rerank_top_n=K, **overrides)
        svc = await Services.create(
            settings, cwd=REPO, gateway=FakeGateway(), with_mcp=False, with_extensions=False
        )
        try:
            mem = svc.memory
            assert mem is not None
            ids: dict[str, int] = {}
            for r in rows:
                for text in [r["fact"], *r.get("distractors", [])]:
                    fid, _ = await mem.add_fact(text, source="eval")
                    ids[text] = fid
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
                hits_at, ndcg, lat = 0, 0.0, []
                skipped_before = mem.rerank_stats.skipped
                for r in rows:
                    started = time.perf_counter()
                    hits = await mem.search(r["query"], limit=K)
                    lat.append((time.perf_counter() - started) * 1000)
                    ranked = [h.id for h in hits if h.kind == "fact"][:K]
                    want = ids[r["fact"]]
                    if want in ranked:
                        hits_at += 1
                        ndcg += 1.0 / math.log2(ranked.index(want) + 2)
                n = len(rows) or 1
                results[name] = {
                    "recall@10": round(hits_at / n, 3),
                    "ndcg@10": round(ndcg / n, 3),
                    "p95_ms": round(p95(lat), 2),
                    "rerank_skipped": mem.rerank_stats.skipped - skipped_before,
                    "cost_per_1k_usd": COST_PER_1K.get(name, 0.0),
                    "detail": status.detail,
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
    args = ap.parse_args(argv)
    overrides: dict[str, Any] = {"rerank_download_models": args.download}
    if args.model:
        overrides["rerank_model"] = args.model
    if args.timeout:
        overrides["rerank_timeout_s"] = args.timeout
    results = asyncio.run(run_eval(args.golden, args.backend, **overrides))
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    n = len(load_golden(args.golden))
    print(f"memory eval: {n} queries, k={K}, fake hashed embeddings")
    print(f"{'backend':<14} {'recall@10':>9} {'nDCG@10':>8} {'p95 ms':>8} {'skipped':>7} {'$/1k':>6}")
    for name, r in results.items():
        if "skipped" in r:
            print(f"{name:<14} {'—':>9} {'—':>8} {'—':>8} {'—':>7} {'—':>6}  unavailable: {r['skipped']}")
            continue
        print(
            f"{name:<14} {r['recall@10']:>9.3f} {r['ndcg@10']:>8.3f} {r['p95_ms']:>8.2f} "
            f"{r['rerank_skipped']:>7} {r['cost_per_1k_usd']:>6.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
