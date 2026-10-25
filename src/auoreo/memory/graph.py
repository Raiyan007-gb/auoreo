"""GraphRAG-lite (spec §25.8): entity graph over facts, communities, 1-hop fan-out. No LLM.

- `fact_entities` holds the rule-based entities of every active fact (memory/entities.py);
  triggers drop the rows when a fact is purged, archived or expires.
- Communities: connected components of the entity co-occurrence graph after pruning edges
  with weight < `min_weight` (level 1). Level 2 merges level-1 components that are linked
  through a high-degree ("hub") entity of the unpruned graph. Summaries are extractive: an
  entity line plus the 3 most-used facts, capped at `cap_tokens` by the gateway counter.
- Search (when `memory.graph_enabled`): facts mentioning a query entity plus their 1-hop
  neighbours (facts sharing an entity with those) form one extra RRF list, weighted ×0.8;
  global queries ("summarize", "overall", …) get the top-2 community summaries appended.

All functions are blocking; `MemoryService` runs them in its worker thread.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from auoreo.memory.entities import extract_entities, query_entities
from auoreo.memory.store import MemoryStore, active

TokenCounter = Callable[[str], int]
GLOBAL_TRIGGER = re.compile(r"\b(overall|summary|summari[sz]e|themes|everything|all my|in general)\b", re.I)
SUMMARY_FACTS = 3
HUB_MIN_DEGREE = 3
HUB_TOP_FRACTION = 0.1


@dataclass(frozen=True)
class Community:
    id: int
    level: int
    entities: list[str]
    fact_ids: list[int]
    summary: str
    mtime: float


def is_global(query: str) -> bool:
    return GLOBAL_TRIGGER.search(query) is not None


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# ------------------------------------------------------------------ indexing
def index_fact(store: MemoryStore, fact_id: int, content: str) -> list[str]:
    """(Re)write the entity rows of one fact; returns the entities."""
    ents = extract_entities(content)
    store.db.execute("DELETE FROM fact_entities WHERE fact_id=?", (fact_id,))
    store.db.executemany(
        "INSERT OR IGNORE INTO fact_entities(fact_id, entity) VALUES (?, ?)", [(fact_id, e) for e in ents]
    )
    store.db.commit()
    return ents


def backfill(store: MemoryStore) -> int:
    """Index active facts that have no entity rows yet (facts written before v2)."""
    rows = store.db.execute(
        f"SELECT id, content FROM facts f WHERE tenant_id=? AND {active('f')} "
        "AND NOT EXISTS (SELECT 1 FROM fact_entities e WHERE e.fact_id = f.id)",
        (store.tenant, time.time()),
    ).fetchall()
    for fid, content in rows:
        index_fact(store, int(fid), str(content))
    return len(rows)


def _active_entities(store: MemoryStore) -> dict[int, set[str]]:
    rows = store.db.execute(
        f"SELECT e.fact_id, e.entity FROM fact_entities e JOIN facts f ON f.id = e.fact_id "
        f"WHERE f.tenant_id=? AND {active('f')}",
        (store.tenant, time.time()),
    ).fetchall()
    out: dict[int, set[str]] = {}
    for fid, ent in rows:
        out.setdefault(int(fid), set()).add(str(ent))
    return out


def entities_of(store: MemoryStore, fact_id: int) -> list[str]:
    rows = store.db.execute("SELECT entity FROM fact_entities WHERE fact_id=? ORDER BY entity", (fact_id,))
    return [str(r[0]) for r in rows.fetchall()]


# ------------------------------------------------------------------ fan-out
def _facts_for(store: MemoryStore, entities: Iterable[str]) -> list[tuple[int, str, int, str]]:
    ents = sorted(set(entities))
    if not ents:
        return []
    marks = ",".join("?" * len(ents))
    return [
        (int(r[0]), str(r[1]), int(r[2]), str(r[3]))
        for r in store.db.execute(
            f"SELECT e.fact_id, e.entity, f.use_count, f.content FROM fact_entities e "
            f"JOIN facts f ON f.id = e.fact_id WHERE e.entity IN ({marks}) AND f.tenant_id=? "
            f"AND {active('f')}",
            (*ents, store.tenant, time.time()),
        ).fetchall()
    ]


def resolve(store: MemoryStore, query: str) -> list[str]:
    """Query candidates (memory/entities.query_entities) that some active fact mentions."""
    cands = query_entities(query)
    known = {e for _, e, _, _ in _facts_for(store, cands)}
    out = [c for c in cands if c in known]
    for word in (c for c in cands if " " not in c and len(c) >= 3 and c.isalpha()):
        # a first or last name in the query reaches the full multi-word entity ("priya" → "priya raman")
        for (ent,) in store.db.execute(
            "SELECT DISTINCT entity FROM fact_entities WHERE entity LIKE ? OR entity LIKE ? LIMIT 8",
            (f"{word} %", f"% {word}"),
        ).fetchall():
            if ent not in out:
                out.append(str(ent))
    return out


def fanout(store: MemoryStore, query: str, limit: int) -> list[int]:
    """1-hop neighbour facts of the query entities: facts that share an entity with a fact
    naming a query entity (the "seeds", which FTS/KNN already reach), ordered by shared
    entity weight then usage, deduped by normalized content (also against the seeds) and
    capped at `limit`."""
    q_ents = set(resolve(store, query))
    if not q_ents:
        return []
    seed_hits: dict[int, list[Any]] = {}
    for fid, _ent, uses, content in _facts_for(store, q_ents):
        entry = seed_hits.setdefault(fid, [0, uses, content])
        entry[0] += 1
    neighbour_w: Counter[str] = Counter()
    for fid in seed_hits:
        for ent in entities_of(store, fid):
            if ent not in q_ents:
                neighbour_w[ent] += 1
    hop: dict[int, list[Any]] = {}
    for fid, ent, uses, content in _facts_for(store, neighbour_w):
        if fid in seed_hits:
            continue
        entry = hop.setdefault(fid, [0, uses, content])
        entry[0] += neighbour_w[ent]
    ordered = sorted(hop.items(), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[0]))
    out: list[int] = []
    seen = {normalize(v[2]) for v in seed_hits.values()}
    for fid, (_w, _uses, content) in ordered:
        key = normalize(content)
        if key in seen:
            continue
        seen.add(key)
        out.append(fid)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ communities
class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, i: str) -> str:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[set[str]]:
        out: dict[str, set[str]] = {}
        for i in self.parent:
            out.setdefault(self.find(i), set()).add(i)
        return list(out.values())


Weights = Counter[tuple[str, str]]


def components(by_fact: dict[int, set[str]], min_weight: int) -> tuple[list[set[str]], Weights]:
    """Level-1 communities (connected components over edges with weight ≥ min_weight)."""
    weights: Counter[tuple[str, str]] = Counter()
    for ents in by_fact.values():
        weights.update(combinations(sorted(ents), 2))
    strong = [pair for pair, w in weights.items() if w >= min_weight]
    uf = _UnionFind({e for pair in strong for e in pair})
    for a, b in strong:
        uf.union(a, b)
    return sorted((g for g in uf.groups() if len(g) > 1), key=lambda g: sorted(g)), weights


def hubs(weights: Counter[tuple[str, str]]) -> set[str]:
    """High-degree entities of the unpruned graph: top 10 % by degree and degree ≥ 3."""
    degree: Counter[str] = Counter()
    for a, b in weights:
        degree[a] += 1
        degree[b] += 1
    if not degree:
        return set()
    ranked = sorted(degree.values(), reverse=True)
    cut = max(HUB_MIN_DEGREE, ranked[max(0, int(len(ranked) * HUB_TOP_FRACTION) - 1)])
    return {e for e, d in degree.items() if d >= cut}


def merge_level2(level1: list[set[str]], weights: Counter[tuple[str, str]]) -> list[set[str]]:
    """Union level-1 components that a hub entity touches (as member or 1-hop neighbour)."""
    comp_of = {e: i for i, comp in enumerate(level1) for e in comp}
    neighbours: dict[str, set[str]] = {}
    for a, b in weights:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    uf = _UnionFind(str(i) for i in range(len(level1)))
    extra: dict[str, set[str]] = {}
    for hub in sorted(hubs(weights)):
        touched = sorted({comp_of[n] for n in neighbours.get(hub, set()) | {hub} if n in comp_of})
        for other in touched[1:]:
            uf.union(str(touched[0]), str(other))
        if len(touched) > 1:
            extra.setdefault(str(touched[0]), set()).add(hub)
    merged: list[set[str]] = []
    for group in uf.groups():
        if len(group) < 2:
            continue
        ents = set().union(*(level1[int(i)] for i in group))
        for i in group:
            ents |= extra.get(i, set())
        merged.append(ents)
    return sorted(merged, key=lambda g: sorted(g))


def cap_tokens(text: str, cap: int, count: TokenCounter) -> str:
    """Longest word-boundary prefix of `text` with ≤ `cap` tokens."""
    if count(text) <= cap:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[:mid] + " …") <= cap:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + " …"


def summarize(
    ents: set[str], fact_ids: list[int], rows: dict[int, dict[str, Any]], cap: int, count: TokenCounter
) -> str:
    """Extractive summary: entity line + the top-3 most-used facts, ≤ `cap` tokens."""
    freq = Counter(e for fid in fact_ids for e in rows[fid]["entities"] if e in ents)
    top_ents = [e for e, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
    best = sorted(fact_ids, key=lambda f: (-int(rows[f]["use_count"]), -float(rows[f]["trust"]), -f))[
        :SUMMARY_FACTS
    ]
    lines = [f"Theme: {', '.join(top_ents)} ({len(fact_ids)} facts)"]
    lines += [f"- {' '.join(str(rows[f]['content']).split())}" for f in best]
    return cap_tokens("\n".join(lines), cap, count)


def build(store: MemoryStore, levels: int, min_weight: int, cap: int, count: TokenCounter) -> list[Community]:
    """Recompute and persist every community (replaces the previous set)."""
    backfill(store)
    by_fact = _active_entities(store)
    rows: dict[int, dict[str, Any]] = {}
    if by_fact:
        marks = ",".join("?" * len(by_fact))
        for fid, content, uses, trust in store.db.execute(
            f"SELECT id, content, use_count, trust FROM facts WHERE id IN ({marks})", tuple(by_fact)
        ).fetchall():
            rows[int(fid)] = {"content": content, "use_count": uses, "trust": trust, "entities": by_fact[fid]}
    level1, weights = components(by_fact, min_weight)
    tiers: list[tuple[int, list[set[str]]]] = [(1, level1)]
    if levels >= 2:
        tiers.append((2, merge_level2(level1, weights)))
    now = time.time()
    store.db.execute("DELETE FROM communities WHERE tenant_id=?", (store.tenant,))
    out: list[Community] = []
    for level, groups in tiers:
        for ents in groups:
            fids = sorted(f for f, fe in by_fact.items() if fe & ents and f in rows)
            if not fids:
                continue
            summary = summarize(ents, fids, rows, cap, count)
            cur = store.db.execute(
                "INSERT INTO communities(tenant_id, level, entities, fact_ids, summary, mtime) "
                "VALUES (?,?,?,?,?,?) RETURNING id",
                (store.tenant, level, json.dumps(sorted(ents)), json.dumps(fids), summary, now),
            )
            out.append(Community(int(cur.fetchone()[0]), level, sorted(ents), fids, summary, now))
    store.db.commit()
    return out


def load(store: MemoryStore, level: int | None = None) -> list[Community]:
    sql = "SELECT id, level, entities, fact_ids, summary, mtime FROM communities WHERE tenant_id=?"
    args: tuple[Any, ...] = (store.tenant,)
    if level is not None:
        sql, args = sql + " AND level=?", (*args, level)
    return [
        Community(int(r[0]), int(r[1]), json.loads(r[2]), json.loads(r[3]), str(r[4]), float(r[5]))
        for r in store.db.execute(sql + " ORDER BY level, id", args).fetchall()
    ]


def is_stale(store: MemoryStore) -> bool:
    """True when no communities exist yet or a fact changed after the last build."""
    built = store.db.execute("SELECT max(mtime) FROM communities WHERE tenant_id=?", (store.tenant,))
    mtime = built.fetchone()[0]
    if mtime is None:
        return True
    changed = store.db.execute("SELECT max(updated_at) FROM facts WHERE tenant_id=?", (store.tenant,))
    last = changed.fetchone()[0]
    return last is not None and float(last) > float(mtime)


def pick(store: MemoryStore, query: str, n: int = 2) -> list[Community]:
    """Top-`n` communities for a global query: entity overlap, then level, then size; a
    level-1 community already contained in a picked level-2 one is skipped."""
    cands = set(query_entities(query))
    ranked = sorted(
        load(store), key=lambda c: (-len(cands & set(c.entities)), -c.level, -len(c.fact_ids), c.id)
    )
    out: list[Community] = []
    for c in ranked:
        if any(set(c.entities) <= set(p.entities) for p in out):
            continue
        out.append(c)
        if len(out) >= n:
            break
    return out
