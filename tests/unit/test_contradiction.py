"""Contradiction flagging: heuristic, event, ledger, curator report; never overwrites."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.memory import curator as curator_mod
from auoreo.memory import ledger
from auoreo.memory.contradiction import contradicts, tokens, top_terms


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        (
            "The staging cluster does not use postgres replicas",
            "The staging cluster uses postgres replicas",
            True,
        ),
        (
            "The staging cluster never runs postgres backups",
            "The staging cluster runs postgres backups",
            True,
        ),
        ("Production cluster runs 5 postgres replicas", "Production cluster runs 3 postgres replicas", True),
        (
            "Production cluster runs 3 postgres replicas",
            "Production cluster runs 3 postgres replicas!",
            False,
        ),
        ("The user does not like coffee", "The user likes tea in the morning", False),  # < 3 shared tokens
        (
            "Staging cluster postgres replicas are healthy",
            "Staging cluster postgres replicas are fast",
            False,
        ),
    ],
)
def test_heuristic(new: str, old: str, expected: bool) -> None:
    assert contradicts(new, old) is expected


def test_tokens_drop_stopwords_and_polarity() -> None:
    assert tokens("The cluster does NOT use Postgres anymore") == {"cluster", "postgres", "anymore"}
    assert top_terms("aa bbb cccc ddddd")[0] == "ddddd"


async def test_add_fact_flags_without_overwriting(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    old, _ = await mem.add_fact("The billing service runs 3 worker replicas in production")
    mark = await services.events.last_seq()
    new, created = await mem.add_fact("The billing service runs 6 worker replicas in production")
    assert created and new != old
    events = [e for e in await services.events.replay(mark) if e.type == "memory.contradiction"]
    assert [(e.aggregate_id, e.data) for e in events] == [("server", {"new_id": new, "existing_id": old})]
    flags = [e for e in ledger.read(services.settings.home) if e["action"] == "flag_contradiction"]
    assert flags and flags[-1]["fact_id"] == new and flags[-1]["existing_id"] == old
    # both facts stay active: flags are advisory
    assert (await mem.get_fact(old) or {})["archived_at"] is None
    assert (await mem.get_fact(old) or {})["valid_to"] is None
    rep = await curator_mod.get(services).run(dry_run=True)
    assert [(c.new_id, c.existing_id) for c in rep.contradictions] == [(new, old)]
    assert rep.report_path and f"fact {new} vs {old}" in read(rep.report_path)
    # resolving one side (forget) clears the pending flag
    await mem.forget(old)
    rep = await curator_mod.get(services).run(dry_run=True)
    assert rep.contradictions == []


async def test_unrelated_fact_is_not_flagged(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await mem.add_fact("The billing service runs 3 worker replicas in production")
    mark = await services.events.last_seq()
    await mem.add_fact("The user enjoys hiking 3 times a month")
    assert not [e for e in await services.events.replay(mark) if e.type == "memory.contradiction"]


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")
