"""I4 memory: v3 schema, CJK trigram FTS, embed_model-filtered KNN + resumable re-embed,
scopes/tenants/decay/contradictions, scrubber, injection excerpt, docs chunking."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from auoreo.core.db import AuditRow
from auoreo.core.diagnostics_i4 import reembed_row
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.memory import migrations
from auoreo.memory.chunking import chunk_docs, looks_like_docs
from auoreo.memory.contradiction import entity_overlap, is_contradiction
from auoreo.memory.reembed import ReembedJob, status_offline
from auoreo.memory.scrubber import StreamingContextScrubber, sanitize_context
from auoreo.memory.service_i4 import TenantDenied, decay
from auoreo.memory.store import MemoryStore, Row
from tests.unit.test_tools_m2 import call, ctx_for


# --------------------------------------------------------------- schema v3
def test_v3_schema(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", dims=8)
    try:
        assert migrations.user_version(store.db) == migrations.VERSION == 3
        cols = migrations._columns(store.db, "facts")
        assert {"scope", "owner_session", "user_id", "expires_at", "half_life_days"} <= cols
        assert {"scope", "owner_session"} <= migrations._columns(store.db, "chunks")
        assert store.trigram and store.tenant_ids() == ["local"]
        assert store.reembed_state() is None
    finally:
        store.close()


# --------------------------------------------------------------------- CJK
async def test_cjk_trigram_search(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await mem.add_fact("数据库迁移之前需要先备份生产环境")
    await mem.add_fact("The weekly sync is on Thursday")
    hits = await mem.search("数据库迁移怎么做", kinds=("fact",))
    assert hits and "数据库迁移" in hits[0].text
    hits = await mem.search("备份", kinds=("fact",))  # 2-char word: LIKE fallback
    assert hits and "备份" in hits[0].text
    await mem.record_message("s1", "user", "東京のオフィスは月曜日に休みです")
    found = await mem.search_sessions("東京のオフィス", session_id="s1")
    assert found and "東京" in found[0].text


# ---------------------------------------------------------------- re-embed
async def test_knn_filters_embed_model_and_reembed_resumes(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = services.memory
    assert mem is not None and mem.store.vec_enabled
    for i in range(5):
        await mem.add_fact(f"fact number {i} about rotating the signing keys")
    old = mem.cfg.embed_model
    emb = (await mem._embed(["rotating signing keys"]) or [[]])[0]
    assert await mem._run(mem.store.knn, "fact", emb, 5, False, old)
    mem.cfg.embed_model = "fake/new-embedder"
    assert await mem._run(mem.store.knn, "fact", emb, 5, False, "fake/new-embedder") == []
    job = ReembedJob(services)
    st = await job.status()
    assert st.stale == 5 and st.state == "pending"
    calls = {"n": 0}
    real = mem._embed

    async def flaky(texts: list[str]) -> list[list[float]] | None:
        calls["n"] += 1
        return None if calls["n"] == 2 else await real(texts)

    monkeypatch.setattr(mem, "_embed", flaky)
    failed = await job.run(batch=2, throttle_s=0)
    assert failed.state == "failed" and failed.done == 2 and failed.stale == 3
    saved = mem.store.reembed_state() or {}
    assert saved["cursor_id"] > 0 and saved["kind"] == "fact"
    done = await job.run(batch=2, throttle_s=0)  # resumes from the cursor
    assert done.state == "done" and done.stale == 0 and done.done == 5
    assert await mem._run(mem.store.knn, "fact", emb, 5, False, "fake/new-embedder")
    offline = status_offline(services.settings)
    assert offline is not None and offline.stale == 0 and offline.state == "done"
    assert reembed_row(services.settings).level == "ok"


def test_reembed_cli_status(services: Services) -> None:
    from auoreo.cli.app import app

    r = CliRunner().invoke(app, ["memory", "reembed", "--status"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["state"] in {"idle", "pending", "done"}


# ------------------------------------------------- scopes, decay, expiry
async def test_session_scope_visible_to_owner_only(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    await mem.add_fact("the scratch branch is called kiwi", scope="session", owner_session="sA")
    assert await mem.search("scratch branch kiwi", session_id="sA")
    assert await mem.search("scratch branch kiwi", session_id="sB") == []
    assert await mem.search("scratch branch kiwi") == []
    await mem.add_fact("old promo code expired", expires_at=time.time() - 5)
    assert await mem.search("promo code expired") == []


def test_decay_factor() -> None:
    now = time.time()
    row = Row("fact", 1, "x", None, 0.5, {"created_at": now - 10 * 86_400, "half_life_days": 10})
    assert decay(row, now) == pytest.approx(0.5)
    assert decay(Row("fact", 2, "x", None, 0.5, {"created_at": now}), now) == 1.0


async def test_decayed_fact_ranks_below_fresh_one(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    old, _ = await mem.add_fact("the staging cluster lives in region eu-west", half_life_days=1)
    mem.store.db.execute("UPDATE facts SET created_at = ? WHERE id = ?", (time.time() - 30 * 86_400, old))
    mem.store.db.commit()
    new, _ = await mem.add_fact("the staging cluster now lives in region us-east")
    hits = await mem.search("staging cluster region", kinds=("fact",))
    assert [h.id for h in hits][:2] == [new, old]


# ---------------------------------------------------------- contradictions
def test_entity_overlap_contradiction() -> None:
    assert entity_overlap("We deploy on Fridays", "We never deploy on Fridays") >= 0.3
    assert is_contradiction("We never deploy on Fridays", "We deploy on Fridays")
    assert not is_contradiction("Lunch is not served", "We deploy on Fridays")
    assert is_contradiction("The API rate limit is 500 requests", "The API rate limit is 100 requests")


async def test_flagged_contradiction_event(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    a, _ = await mem.add_fact("Payments service deploys on Fridays")
    b, _ = await mem.add_fact("Payments service never deploys on Fridays")
    assert a in await mem.flag_contradictions(b, "Payments service never deploys on Fridays")


# ------------------------------------------------------------------ tenants
async def test_team_tenant_provisioned_and_cross_tenant_denied(services: Services, workdir: Path) -> None:
    mem = services.memory
    assert mem is not None
    lead = await services.create_session()
    team = await services.ext["teams"].create_team(lead.id, "alpha")
    await services.ext["tenants"].drain()
    assert f"team:{team.id}" in mem.store.tenant_ids()
    await mem.authorize_tenant(f"team:{team.id}", session_id=lead.id, tool="knowledge_search")
    other = mem.for_tenant(f"team:{team.id}")
    await other.add_fact("alpha team retro notes live in the wiki")
    assert await other.search("alpha retro notes wiki") and not await mem.search("alpha retro notes wiki")
    outsider = await services.create_session()
    with pytest.raises(TenantDenied):
        await mem.authorize_tenant(f"team:{team.id}", session_id=outsider.id, tool="knowledge_search")
    ctx = ctx_for(services, workdir)
    text, err = await call(services, ctx, "knowledge_search", {"query": "retro", "tenant": "team:zzz"})
    assert err and "tenant-deny" in text
    async with services.db.session() as s:
        rows = list((await s.execute(select(AuditRow).where(AuditRow.reason == "tenant-deny"))).scalars())
    assert rows and services.audit.plain_detail(rows[-1])["decisionReason"] == "tenant-deny"


# ------------------------------------------------------------------ scrubber
def test_sanitize_context_three_patterns() -> None:
    assert sanitize_context("a <memory-context>x</memory-context>b") == "a b"
    assert sanitize_context("keep <memory-context>never closed") == "keep "
    assert sanitize_context("stray </memory-context> tag") == "stray  tag"


def test_streaming_scrubber_boundaries() -> None:
    sc = StreamingContextScrubber()
    pieces = ["Hello <mem", "ory-context>sec", "ret</memory-", "context> world"]
    out = "".join(sc.feed(p) for p in pieces) + sc.flush()
    assert out == "Hello  world"
    sc.reset()
    assert sc.feed("open <memory-context>leak") == "open " and sc.flush() == ""
    assert sc.feed("a <me") == "a " and sc.flush() == "<me"  # a held partial tag is released


async def test_runner_scrubs_output_and_input(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    gateway.queue(Scripted(text="Sure. <memory-context>- [fact:1] secret</memory-context>Done."))
    await services.coordinator.submit(
        sess.id, "hi <memory-context>pasted</memory-context>there", interactive=False
    )
    await services.coordinator.wait(sess.id, 10)
    rt = await services.coordinator.runtime(sess.id)
    assert "pasted" not in str(rt.history[0]["content"])
    assistant = [m for m in rt.history if m["role"] == "assistant"][-1]
    assert assistant["content"] == "Sure. Done."
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}), limit=10_000)
    deltas = "".join(e.data.get("text", "") for e in events if e.type == "message.delta")
    assert "secret" not in deltas


# ----------------------------------------------------------------- injection
async def test_injection_excerpt_capped_and_guidance(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    from auoreo.memory.files import memory_dir

    mdir = memory_dir(services.settings.home)
    (mdir / "MEMORY.md").write_text("# Memory index\n" + "- [t](topics/t.md) — hook\n" * 60)
    (mdir / "USER.md").write_text("# User\n" + "- likes tea\n" * 100)
    block = await mem.context_block("tea")
    assert len(block) <= services.settings.i4.memory.injection_chars + 200
    assert "Only part loaded" in block
    from auoreo.harness.orchestrator import build_spec

    prompt = build_spec(services.harness, "default", "default", "manual").system_prompt
    assert "act as if memory were empty" in prompt and "Verify them" in prompt


# -------------------------------------------------------------- docs chunking
def test_docs_chunking_heading_aware_and_code_whole() -> None:
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(300)) + "\n```"
    doc = (
        "# Guide\n\nIntro paragraph.\n\n## Install\n\n"
        + "Install words here. " * 50
        + f"\n\n{code}\n\n## Use\n\nRun it."
    )

    def count(t: str) -> int:
        return len(t.split())

    chunks = chunk_docs(doc, 120, 0.15, count)
    with_code = [c for c in chunks if "```python" in c.text]
    assert len(with_code) == 1 and with_code[0].text.count("x299 = 299") == 1  # never split
    assert any(c.text.startswith("## Install") for c in chunks if "Install words" in c.text)
    use = next(c for c in chunks if "Run it." in c.text)
    assert use.text.startswith("## Use")
    assert looks_like_docs(doc) and not looks_like_docs("plain words only", "notes.txt")


async def test_ingest_docs_tier(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    doc = "# Ops\n\n## Restarts\n\nRestart the worker with `make restart`.\n\n```sh\nmake restart\n```\n"
    assert await mem.ingest(doc, "ops.md", tier="docs") >= 1
    hits = await mem.search("restart the worker", kinds=("chunk",))
    assert hits and "make restart" in hits[0].text


def _unused(*_: Any) -> None:  # keep sqlite3 import used for type hints in older pythons
    sqlite3.connect(":memory:").close()
