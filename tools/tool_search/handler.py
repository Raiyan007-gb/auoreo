"""BM25 over deferred tools' name + description + search_hint. Exact name match wins."""

import math
import re
from collections import Counter

from auoreo.tools.context import TOOLS_LOADED_PREFIX, ToolContext


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


async def run(args: dict, ctx: ToolContext) -> str:
    spec = ctx.services.get("spec")
    pool = list(spec.deferred) if spec is not None else []
    if not pool:
        return "no additional tools are available"
    docs = {
        t.name: _tokens(
            f"{t.name} {t.name.replace('_', ' ')} {t.manifest.description} {t.manifest.search_hint}"
        )
        for t in pool
    }
    query = _tokens(args["query"])
    avg = sum(len(d) for d in docs.values()) / len(docs)
    df = Counter(term for d in docs.values() for term in set(d))
    scores: dict[str, float] = {}
    for name, doc in docs.items():
        tf = Counter(doc)
        s = 0.0
        for q in query:
            if q in tf:
                idf = math.log(1 + (len(docs) - df[q] + 0.5) / (df[q] + 0.5))
                s += idf * tf[q] * 2.2 / (tf[q] + 1.2 * (0.25 + 0.75 * len(doc) / avg))
        if args["query"].strip() == name:
            s += 100
        if s > 0:
            scores[name] = s
    best = sorted(scores, key=lambda n: scores[n], reverse=True)[: int(args.get("limit", 5))]
    if not best:
        return f"no tools match {args['query']!r}"
    by_name = {t.name: t for t in pool}
    ctx.state.loaded_tools.update(best)
    return (
        TOOLS_LOADED_PREFIX
        + "\n"
        + "\n".join(f"- {n}: {by_name[n].manifest.description.strip()[:200]}" for n in best)
    )
