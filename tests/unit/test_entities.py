"""Rule-based entity extraction (memory/entities.py) and its storage on fact writes."""

from __future__ import annotations

import pytest

from auoreo.core.services import Services
from auoreo.memory import graph
from auoreo.memory.entities import MAX_ENTITIES, canonical, extract_entities, query_entities


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Maria Silva works at Acme Robotics.", ["maria silva", "acme robotics"]),
        ("Ping @maria_s in #infra about it.", ["@maria_s", "#infra"]),
        ("Settings live in src/auoreo/core/settings.py today.", ["src/auoreo/core/settings.py"]),
        ("Docs are at https://www.example.com/a/b?x=1 now.", ["example.com"]),
        ('Run "make ship" or `uv sync` first.', ["make ship", "uv sync"]),
        ("We use LazyVim and the snake_case_name helper.", ["lazyvim", "snake_case_name"]),
        ("The Rust Book is good.", ["rust book"]),
        ("I met Bob. Then Alice and Bob went to New York City.", ["bob", "alice", "new york city"]),
        ("Lena's brother is Marco.", ["lena", "marco"]),
        ("Schedule meetings after lunch.", []),
        ("the user likes tea on monday", []),
        ("Use the config.toml file.", ["config.toml"]),
    ],
)
def test_extract_entities(text: str, expected: list[str]) -> None:
    assert sorted(extract_entities(text)) == sorted(expected)


def test_entities_are_canonical_deduped_and_capped() -> None:
    assert canonical("  Acme   Robotics's, ") == "acme robotics"
    assert extract_entities("Acme visits ACME and acme. Then Acme again.") == ["acme"]
    many = " ".join(f"Name{chr(65 + i)}x Corp{chr(65 + i)}y and" for i in range(20))
    ents = extract_entities("We met " + many)
    assert len(ents) == MAX_ENTITIES and len(set(ents)) == MAX_ENTITIES
    assert all(e == e.lower() for e in ents)


def test_url_text_is_not_reparsed_as_a_path_or_name() -> None:
    assert extract_entities("See https://github.com/Org/Repo/blob/main/x.py please.") == ["github.com"]


def test_query_entities_include_lowercase_ngrams() -> None:
    cands = query_entities("what pet does lena's brother own")
    assert "lena" in cands and "brother" in cands and "does" not in cands


async def test_add_replace_forget_purge_maintain_entity_rows(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fid, _ = await mem.add_fact("Tomasz works for Northwind Analytics.")
    assert graph.entities_of(mem.store, fid) == ["northwind analytics", "tomasz"]
    new = await mem.replace_fact(fid, "Tomasz works for Bluefin Studio.")
    assert new is not None and new != fid
    assert graph.entities_of(mem.store, fid) == []
    assert graph.entities_of(mem.store, new) == ["bluefin studio", "tomasz"]
    assert await mem.forget(new)
    assert graph.entities_of(mem.store, new) == []
    again, created = await mem.add_fact("Tomasz works for Bluefin Studio.")  # revive re-indexes
    assert again == new and not created
    assert graph.entities_of(mem.store, new) == ["bluefin studio", "tomasz"]
    assert await mem.purge(new)
    assert graph.entities_of(mem.store, new) == []
