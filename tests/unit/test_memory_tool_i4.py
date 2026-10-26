"""I4 §11.2/§11.3/§11.8: topic files, memory_read, drift guard, taxonomy, result semantics."""

from __future__ import annotations

from pathlib import Path

from auoreo.core.services import Services
from auoreo.memory.files import memory_dir
from auoreo.memory.topics import parse_topic
from tests.unit.test_tools_m2 import call, ctx_for


async def test_topic_write_index_and_memory_read(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    op = {
        "action": "add",
        "target": "topic",
        "name": "Release process",
        "description": "how we cut and ship releases",
        "type": "project",
        "content": "1. tag\n2. build\n3. publish to the registry",
    }
    text, err = await call(services, ctx, "memory", {"operations": [op]})
    assert not err, text
    mdir = memory_dir(services.settings.home)
    topic = (mdir / "topics" / "release-process.md").read_text()
    assert parse_topic(topic)["type"] == "project" and "publish to the registry" in topic
    index = (mdir / "MEMORY.md").read_text()
    assert "- [Release process](topics/release-process.md) — how we cut and ship releases" in index
    assert "MEMORY.md updated (" in text and "% used)" in text
    body, err = await call(services, ctx, "memory_read", {"name": "release-process"})
    assert not err and "publish to the registry" in body
    listing, _ = await call(services, ctx, "memory_read", {})
    assert "topics/release-process.md [project]" in listing
    text, err = await call(services, ctx, "memory_read", {"name": "nope"})
    assert err and "topics/release-process.md" in text
    rm = {"action": "remove", "target": "topic", "name": "Release process"}
    text, err = await call(services, ctx, "memory", {"operations": [rm]})
    assert not err and "archived" in text and "release-process" not in (mdir / "MEMORY.md").read_text()


async def test_fact_type_scope_and_expiry(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    ops = [
        {"action": "add", "target": "fact", "content": "User is called Ana", "type": "user"},
        {"action": "add", "target": "fact", "content": "Docs live on the vendor portal", "type": "reference"},
        {"action": "add", "target": "fact", "content": "temp branch is lime", "scope": "session"},
        {"action": "add", "target": "fact", "content": "Freeze until launch", "expires_at": "2999-01-01"},
    ]
    text, err = await call(services, ctx, "memory", {"operations": ops})
    assert not err, text
    assert "(type=user, scope=user)" in text and "(type=reference, scope=team)" in text
    assert "scope=session" in text and "active facts stored" in text
    mem = services.memory
    assert mem is not None
    rows = {r["content"]: r for r in mem.store.fact_rows()}
    assert rows["User is called Ana"]["category"] == "user"
    assert rows["temp branch is lime"]["owner_session"] == "s1"
    assert rows["Freeze until launch"]["expires_at"] > 0
    bad = [{"action": "add", "target": "fact", "content": "x y z", "type": "gossip"}]
    text, err = await call(services, ctx, "memory", {"operations": bad})
    assert err  # schema enum rejects it


async def test_miss_lists_current_entries_and_failure_cap(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    await call(
        services, ctx, "memory", {"operations": [{"action": "add", "target": "user", "content": "likes tea"}]}
    )
    miss = {"action": "replace", "target": "user", "old_text": "coffee", "content": "likes coffee"}
    text, err = await call(services, ctx, "memory", {"operations": [miss]})
    assert err and "current_entries" in text and "likes tea" in text
    text, err = await call(services, ctx, "memory", {"operations": [miss]})
    assert err and "stop retrying" not in text
    text, err = await call(services, ctx, "memory", {"operations": [miss]})
    assert err and "failure 3/3" in text
    ok = {"action": "add", "target": "user", "content": "likes cake"}
    text, err = await call(services, ctx, "memory", {"operations": [ok]})
    assert err and "stop retrying and answer" in text  # capped for the rest of the turn
    fresh = ctx_for(services, workdir)  # a new turn gets a new budget
    text, err = await call(services, fresh, "memory", {"operations": [ok]})
    assert not err and "USER.md updated" in text


async def test_drift_guard_snapshots_outside_edits_and_refuses_broken_files(
    services: Services, workdir: Path
) -> None:
    ctx = ctx_for(services, workdir)
    mdir = memory_dir(services.settings.home)
    add = {"action": "add", "target": "user", "content": "uses vim"}
    assert not (await call(services, ctx, "memory", {"operations": [add]}))[1]
    (mdir / "USER.md").write_text((mdir / "USER.md").read_text() + "- hand edit\n")
    text, err = await call(services, ctx, "memory", {"operations": [{**add, "content": "uses tmux"}]})
    assert not err and "outside edit saved as USER.md.bak." in text
    assert list(mdir.glob("USER.md.bak.*"))
    topic = {"action": "add", "target": "topic", "name": "infra", "description": "d", "content": "c"}
    assert not (await call(services, ctx, "memory", {"operations": [topic]}))[1]
    (mdir / "topics" / "infra.md").write_text("no frontmatter at all")
    text, err = await call(services, ctx, "memory", {"operations": [topic]})
    assert err and "unparseable" in text and list((mdir / "topics").glob("infra.md.bak.*"))
