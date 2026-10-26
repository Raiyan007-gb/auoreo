# Spike 2: rerank + embed + chunk + fusion (spec §22-2)

**Status:** decided for the default; the full bake-off matrix is **not run here** (no paid
embedding keys and no GPU in the dev container).

## What exists

| Axis | Shipped default | Alternatives in the tree |
|---|---|---|
| Embed | `openrouter/openai/text-embedding-3-small` (`memory.embed_model`), FTS-only fallback when unavailable | any LiteLLM embedding id (`AUOREO_EMBED_MODEL` + `AUOREO_EMBED_DIMS`); `bge-m3` / `qwen3-embedding` via an OpenAI-compatible local server |
| Rerank | `none` | `backends/rerank/cohere` (v3.5/v4 API, cassette-tested), `cross_encoder` (local, `rerank-local` extra, torch), `fake` |
| Chunk | 512 tokens / 50 overlap, `chunk_strategy: early` | `late` (contextual chunk embedding approximation, `memory/contextual.py`) |
| Fusion | RRF, `rrf_k = 60` over FTS5 + sqlite-vec | — |

## Measured here

`scripts/eval_memory.py` on the generated 230-row golden set (`evals/memory/golden.jsonl`) with
fake hashed embeddings and `--backend none` is a CI gate (`evals/memory/gate.json`:
recall@10 ≥ 0.90, p95 ≤ 250 ms, per-category floors). It proves the FTS + RRF pipeline and
catches regressions; it does **not** rank embedding models, because hashed embeddings carry no
semantics.

## Not run here

- MTEB-style comparison of `text-embedding-3-small` vs `bge-m3` vs `qwen3-embedding`
  (needs keys / a GPU host);
- rerank quality vs cold start for `qwen3-0.6b-q8_0` vs `bge-reranker-v2` vs Cohere v4 on the
  top-30 (needs the models);
- 900/15 % structural chunking and z-normed linear fusion: not implemented, so not compared.

## Decision

Keep the qmd-baseline-compatible default (RRF-60, 512/50, embedding optional, rerank off).
A challenger must beat it on the golden set with real embeddings (`--backend` with a real
model) on recall@10 / nDCG and p95, and the result gets recorded here before the default moves.
