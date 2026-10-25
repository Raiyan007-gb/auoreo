"""GraphRAG-lite (memory/graph.py): communities, levels, summaries, fan-out, global queries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.core.services import Services
from auoreo.memory import curator, graph
from auoreo.memory.service import rrf

REPO = Path(__file__).resolve().parents[2]

HOP = [
    "Lena's brother is Marco, a carpenter in Bologna.",
    "Marco adopted a greyhound named Biscuit last spring.",
    "The Orion project is owned by Kestrel Labs.",
    "Kestrel Labs builds everything in Elixir and Phoenix.",
    "Dogs are not allowed in the co-working space.",
    "Read replicas are rebuilt every quarter.",
]
ATLAS = [
    "The Atlas migration moves the Billing API from Heroku to Fly.io.",
    "Atlas cutover for the Billing API is planned for 14 November.",
    "Atlas rollback keeps Heroku warm for the Billing API for a week.",
]


async def _add(services: Services, texts: list[str]) -> dict[str, int]:
    mem = services.memory
    assert mem is not None
    return {t: (await mem.add_fact(t))[0] for t in texts}


# ------------------------------------------------------------------ pure graph
def test_components_prune_weak_edges_and_level2_merges_through_a_hub() -> None:
    by_fact = {
        1: {"a", "b"},
        2: {"a", "b"},
        3: {"c", "d"},
        4: {"c", "d"},
        5: {"h", "a"},
        6: {"h", "c"},
        7: {"h", "x"},
        8: {"h", "y"},
    }
    level1, weights = graph.components(by_fact, min_weight=2)
    assert level1 == [{"a", "b"}, {"c", "d"}]  # weight-1 edges (h–*) are pruned
    assert graph.hubs(weights) == {"h"}
    assert graph.merge_level2(level1, weights) == [{"a", "b", "c", "d", "h"}]
    assert graph.components(by_fact, min_weight=3)[0] == []


def test_cap_tokens_respects_the_counter() -> None:
    def count(t: str) -> int:
        return len(t.split())

    text = " ".join(f"w{i}" for i in range(100))
    capped = graph.cap_tokens(text, 10, count)
    assert count(capped) <= 10 and capped.endswith("…") and capped.startswith("w0 w1")
    assert graph.cap_tokens("short", 10, count) == "short"


@pytest.mark.parametrize(
    ("query", "is_global"),
    [
        ("give me an overall summary", True),
        ("summarize my week", True),
        ("summarise the themes", True),
        ("what are all my projects", True),
        ("in general what do I like", True),
        ("where does Marco live", False),
        ("summaries", False),
    ],
)
def test_global_trigger(query: str, is_global: bool) -> None:
    assert graph.is_global(query) is is_global


def test_weighted_rrf_scales_one_list() -> None:
    scores = rrf([[("fact", 1)], [("fact", 1), ("fact", 2)]], 60, [1.0, 0.8])
    assert scores[("fact", 1)] == pytest.approx(1 / 61 + 0.8 / 61)
    assert scores[("fact", 2)] == pytest.approx(0.8 / 62)


# ------------------------------------------------------------------ build / levels / summaries
async def test_build_levels_and_extractive_summary(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    ids = await _add(services, ATLAS + HOP)
    built = await mem.build_graph()
    lvl1 = [c for c in built if c.level == 1]
    assert [c.entities for c in lvl1] == [["atlas", "billing api", "heroku"]]
    assert lvl1[0].fact_ids == sorted(ids[t] for t in ATLAS)
    assert lvl1[0].summary.startswith("Theme: ") and "Billing API" in lvl1[0].summary
    assert [c.id for c in await mem.communities(1)] == [lvl1[0].id]
    services.settings.memory.graph_levels = 1
    assert {c.level for c in await mem.build_graph()} == {1}


async def test_summary_takes_top3_most_used_and_caps_tokens(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    texts = [f"Quasar Team and Nebula Group shipped release number {i} {'padding ' * 60}" for i in range(6)]
    ids = await _add(services, texts)
    used = [ids[texts[4]], ids[texts[5]], ids[texts[1]]]
    for _ in range(3):
        mem.store.bump_use(used)
    services.settings.memory.graph_summary_tokens = 600
    [community] = [c for c in await mem.build_graph() if c.level == 1]
    model = services.settings.model or "default"
    assert services.gateway.count_tokens(model, community.summary) <= 600
    lines = community.summary.splitlines()
    assert len(lines) == 4  # theme line + 3 facts
    for i in (4, 5, 1):
        assert f"release number {i} " in community.summary
    services.settings.memory.graph_summary_tokens = 40
    [small] = [c for c in await mem.build_graph() if c.level == 1]
    assert services.gateway.count_tokens(model, small.summary) <= 40 and small.summary.endswith("…")


# ------------------------------------------------------------------ search integration
async def test_fanout_reaches_one_hop_neighbours_with_weighted_score(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    ids = await _add(services, HOP)
    target = ids["Marco adopted a greyhound named Biscuit last spring."]
    assert graph.fanout(mem.store, "what pet does Lena's brother own", 12) == [target]
    off = await mem.search("what pet does Lena's brother own", limit=3)
    services.settings.memory.graph_enabled = True
    on = await mem.search("what pet does Lena's brother own", limit=3)
    assert target in [h.id for h in on]
    before = {h.id: h.score for h in off}
    after = {h.id: h.score for h in on}
    bump = 0.8 / (services.settings.memory.rrf_k + 1) * 0.5  # list weight × trust 0.5
    if target in before:
        assert after[target] == pytest.approx(before[target] + bump)
    else:
        assert after[target] >= bump
    assert (await mem.stats())["graph_hit"] == 1


async def test_fanout_dedupes_by_content_and_respects_limit(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await _add(services, ["Zara Okoye mentors the Falcon squad."])
    await _add(services, [f"Falcon squad owns service number {i}." for i in range(20)])
    await _add(services, ["Falcon squad uses Svelte.", "Falcon squad uses Svelte!"])  # newest first
    fan = graph.fanout(mem.store, "who does Zara mentor", 12)
    texts = [(await mem.get_fact(i) or {})["content"] for i in fan]
    assert len(fan) == 12
    assert sum(1 for t in texts if graph.normalize(t) == "falcon squad uses svelte") == 1


async def test_global_query_appends_top2_community_summaries(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await _add(services, ATLAS + [f"Lisbon flat note {i} with Senhora Costa." for i in range(3)])
    services.settings.memory.graph_enabled = True
    await mem.build_graph()
    hits = await mem.search("give me an overall summary of the Atlas migration", limit=8)
    themes = [h for h in hits if h.kind == "community"]
    assert 1 <= len(themes) <= 2 and hits[-len(themes) :] == themes
    assert "Atlas" in themes[0].text and themes[0].citation.startswith("community:")
    assert [h for h in hits if h.kind == "fact"]
    plain = await mem.search("when is the Atlas cutover", limit=8)
    assert not [h for h in plain if h.kind == "community"]
    block = await mem.context_block("summarize everything about the Atlas migration")
    assert "themes" in block and "community:" in block


async def test_off_by_default_results_are_identical(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    assert services.settings.memory.graph_enabled is False
    await _add(services, HOP + ATLAS)
    await mem.build_graph()
    queries = ["what pet does Lena's brother own", "overall summary of the Atlas migration", "Orion"]
    with_graph_tables = [await mem.search(q) for q in queries]
    mem.store.db.execute("DELETE FROM fact_entities")
    mem.store.db.execute("DELETE FROM communities")
    mem.store.db.commit()
    without = [await mem.search(q) for q in queries]
    assert [[(h.kind, h.id, h.score) for h in r] for r in with_graph_tables] == [
        [(h.kind, h.id, h.score) for h in r] for r in without
    ]
    assert all(h.kind != "community" for r in without for h in r)
    assert (await mem.stats())["graph_hit"] == 0


async def test_curator_refreshes_graph_only_when_enabled_and_stale(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    cur = curator.get(services)
    await _add(services, ATLAS)
    assert await cur.refresh_graph() is False  # disabled
    services.settings.memory.graph_enabled = True
    assert await cur.refresh_graph() is True
    assert await cur.refresh_graph() is False  # nothing changed since the build
    await _add(services, ["Atlas rollout dashboard lives in Grafana."])
    assert await cur.refresh_graph() is True


# ------------------------------------------------------------------ CLI + eval
def test_cli_graph_build_and_show(monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    cli = CliRunner()
    for text in ATLAS:
        assert cli.invoke(app, ["memory", "add", text]).exit_code == 0
    built = cli.invoke(app, ["memory", "graph", "build"])
    assert built.exit_code == 0, built.output
    data = json.loads(built.output)
    assert data["communities"] >= 1 and data["graph_enabled"] is False
    shown = cli.invoke(app, ["memory", "graph", "show", "--level", "1"])
    assert shown.exit_code == 0, shown.output
    [row] = json.loads(shown.output)
    assert row["level"] == 1 and "atlas" in row["entities"] and row["facts"] == 3


def test_eval_graph_raises_entity_hop_recall() -> None:
    import asyncio

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import eval_memory
    finally:
        sys.path.remove(str(REPO / "scripts"))
    base = asyncio.run(eval_memory.run_eval(only=["none"]))["none"]
    with_graph = asyncio.run(eval_memory.run_eval(only=["none"], graph=True))["none"]
    hop_base = base["subsets"]["entity_hop"]["recall@10"]
    hop_graph = with_graph["subsets"]["entity_hop"]["recall@10"]
    assert hop_graph > hop_base
    assert with_graph["subsets"]["base"]["recall@10"] >= base["subsets"]["base"]["recall@10"]
    assert with_graph["graph_hit"] > 0 and base["graph_hit"] == 0
