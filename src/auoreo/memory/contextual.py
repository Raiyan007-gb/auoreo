"""`memory.chunk_strategy: late` — contextual chunk embedding (spec §25.8).

True *late chunking* runs the whole document through a long-context embedding model once and
mean-pools the **token** embeddings inside each chunk span, so every chunk vector is
conditioned on the full document. The LiteLLM embedding API (and the providers behind it)
returns one pooled vector per input string and exposes no token-level output, so that is
not implementable here.

What this module does instead — the honest approximation, usually called *contextual chunk
embedding*: every span is embedded as its own input, prefixed by the span text and followed
by its surrounding document context (the whole document when it fits in
`late_max_chars`, else a `WINDOW_CHARS` window centred on the span). All inputs of a
document go out in one batched `gateway.embed` call (split only when a batch would exceed
`BATCH_CHARS`). The stored chunk text, its FTS row and the citation are unchanged; only the
vector differs. Provenance (`chunk_strategy`, `span_start`, `span_end`) is stored with each
chunk. The setting keeps the spec's name `late` because it selects the same intent —
document-conditioned chunk vectors with exact span provenance — while this docstring and
the provenance make the mechanism explicit.
"""

from __future__ import annotations

from auoreo.memory.chunking import Chunk

WINDOW_CHARS = 8000
BATCH_CHARS = 400_000


def context_window(text: str, start: int, end: int, max_chars: int) -> str:
    """Document context for span [start, end): all of it when it fits, else a window."""
    if len(text) <= max_chars:
        return text
    size = min(WINDOW_CHARS, max_chars)
    mid = (start + end) // 2
    lo = max(0, min(mid - size // 2, len(text) - size))
    return text[lo : lo + size]


def contextual_inputs(text: str, chunks: list[Chunk], max_chars: int) -> list[str]:
    out: list[str] = []
    for c in chunks:
        ctx = context_window(text, max(0, c.span_start), max(c.span_start, c.span_end), max_chars)
        out.append(f"{c.text}\n\n[document context]\n{ctx}")
    return out


def batches(inputs: list[str], max_chars: int = BATCH_CHARS) -> list[list[str]]:
    """Group inputs into as few embed calls as possible under `max_chars` per call."""
    out: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for item in inputs:
        if cur and size + len(item) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += len(item)
    if cur:
        out.append(cur)
    return out
