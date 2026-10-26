"""H2: the agent-facing `cronjob` tool + cron store, the 3-minute job hard interrupt default,
and kanban show/link/attach/unblock (spec §9, §16.6, §16.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.automation.cron_store import CronStoreError, create, next_fires, normalize, plan, remove
from auoreo.automation.scheduler import scheduler_of
from auoreo.core.db import DeliveryRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.harness.manifests import JobManifest
from auoreo.harness.orchestrator import build_spec
from auoreo.teams.kanban import KanbanError, KanbanStore
from tests.conftest import write
from tests.teams_helpers import call_tool, new_parent, tool_ctx


# ---------------------------------------------------------------- cron store
def test_normalize_reminder_and_next_fires() -> None:
    now = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    assert normalize("in 20m", now) == "2026-10-26T08:20:00+00:00"
    assert normalize("every 2h", now) == "every 2h"
    p = plan(
        {"name": "standup", "description": "d", "schedule": "0 9 * * 1-5", "prompt": "p", "tz": "UTC"}, now
    )
    assert p.schedule.kind == "cron" and len(p.next_fires) == 5
    assert p.next_fires[0] == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # a Monday
    assert all(f.weekday() < 5 for f in p.next_fires)
    p2 = plan({"name": "ping", "description": "d", "schedule": "every 30m", "prompt": "p"}, now)
    assert (p2.next_fires[1] - p2.next_fires[0]) == timedelta(minutes=30)
    one = plan({"name": "remind", "description": "tea", "schedule": "in 10m", "prompt": "tea"}, now)
    assert one.schedule.kind == "date" and len(next_fires(one.schedule, now)) == 1
    assert one.manifest.description == "tea (in 10m)" and one.catch_up.startswith("one-shot")
    with pytest.raises(CronStoreError, match="invalid cron"):
        plan({"name": "bad", "description": "d", "schedule": "99 * * * *", "prompt": "p"}, now)
    with pytest.raises(CronStoreError, match="in the past"):
        plan({"name": "old", "description": "d", "schedule": "2020-01-01T00:00:00+00:00", "prompt": "p"}, now)
    with pytest.raises(CronStoreError, match="invalid job"):
        plan({"name": "Bad-Name", "description": "d", "schedule": "every 1h", "prompt": "p"}, now)


async def test_cronjob_tool_create_list_remove(services: Services) -> None:
    parent = await new_parent(services)
    ctx = tool_ctx(services, parent)
    args = {"action": "create", "name": "tea", "schedule": "in 15m", "prompt": "Remind me about tea"}
    text, err = await call_tool(services, ctx, "cronjob", {**args, "dry_run": True})
    assert not err and "dry run (nothing saved)" in text and "date" in text
    assert "tea" not in services.harness.jobs

    text, err = await call_tool(services, ctx, "cronjob", args)
    assert not err and "created job 'tea'" in text, text
    path = services.settings.home / "jobs" / "tea.yaml"
    assert path.is_file() and path.read_text(encoding="utf-8").startswith("# created by AUOREO")
    assert "tea" in services.harness.jobs and services.harness.jobs["tea"].manifest.prompt == args["prompt"]
    text, err = await call_tool(services, ctx, "cronjob", {"action": "list"})
    assert not err and "- tea:" in text
    text, err = await call_tool(services, ctx, "cronjob", args)
    assert err and "already exists" in text

    text, err = await call_tool(services, ctx, "cronjob", {"action": "remove", "name": "tea"})
    assert not err and not path.exists() and "tea" not in services.harness.jobs
    text, err = await call_tool(services, ctx, "cronjob", {"action": "create", "name": "x"})
    assert err and "needs schedule, prompt" in text
    kinds = [e.type for e in await services.events.replay(0, frozenset({"server"}))]
    assert "cron.created" in kinds and "cron.removed" in kinds


async def test_cronjob_create_asks_and_list_is_allowed(services: Services) -> None:
    tool = services.harness.tools["cronjob"]
    assert tool.manifest.default_action == "ask" and tool.manifest.pattern_arg == "action"
    spec = build_spec(services.harness, "default", "default", "manual")
    engine = PermissionEngine(list(spec.rules))
    assert engine.evaluate("cronjob", "list", "ask").action == "allow"
    assert engine.evaluate("cronjob", "create", "ask").action == "ask"


async def test_only_runtime_jobs_are_removable(services: Services, workdir: Path) -> None:
    write(
        workdir / ".auoreo" / "jobs" / "proj.yaml",
        'name: proj\ndescription: d\nschedule: "every 1h"\nprompt: "p"\n',
    )
    services.harness.reload()
    with pytest.raises(CronStoreError, match="outside"):
        await remove(services, "proj")
    with pytest.raises(CronStoreError, match="unknown job"):
        await remove(services, "ghost")


async def test_job_hard_interrupt_defaults_to_three_minutes(services: Services) -> None:
    assert JobManifest.model_fields["timeout_s"].default is None
    assert services.settings.teams2.job_timeout_s == 180
    await create(services, {"name": "quick", "description": "d", "schedule": "every 1h", "prompt": "hi"})
    await create(
        services,
        {"name": "slow", "description": "d", "schedule": "every 1h", "prompt": "hi", "timeout_s": 600},
    )
    sched = scheduler_of(services)
    for name in ("quick", "slow"):
        run_id = await sched.start_run(name)
        assert run_id is not None
        await sched.execute(run_id)
    async with services.db.session() as s:
        rows = {r.source: r for r in (await s.execute(select(DeliveryRow))).scalars()}
    assert rows["quick"].payload["timeout_s"] == 180 and rows["slow"].payload["timeout_s"] == 600


# ---------------------------------------------------------------- kanban
def kb_of(svc: Services) -> KanbanStore:
    kb = svc.ext["teams"].kanban
    assert isinstance(kb, KanbanStore)
    return kb


async def test_kanban_link_attach_show(services: Services) -> None:
    kb = kb_of(services)
    a = await kb.create("b", "build api")
    b = await kb.create("b", "write docs")
    await kb.link(b.id, "blocks", a.id, "w1")
    await kb.attach(a.id, "docs/api.md", "draft spec", "w1")
    await kb.comment(a.id, "w1", "started")
    shown = await kb.show(a.id)
    assert shown["links"] == [{"kind": "blocks<-", "task_id": b.id}]
    assert shown["attachments"] == [{"ref": "docs/api.md", "note": "draft spec", "by": "w1"}]
    assert [c["text"] for c in shown["comments"]] == ["started"]  # structured records stay hidden
    assert (await kb.show(b.id))["links"] == [{"kind": "blocks", "task_id": a.id, "by": "w1"}]
    with pytest.raises(KanbanError, match="unknown link kind"):
        await kb.link(a.id, "owns", b.id)
    with pytest.raises(KanbanError, match="itself"):
        await kb.link(a.id, "relates", a.id)

    parent = await new_parent(services)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(services, ctx, "kanban_show", {"task_id": a.id})
    assert not err and "attachment: docs/api.md — draft spec" in text and f"blocks<- #{b.id}" in text
    text, err = await call_tool(
        services, ctx, "kanban_link", {"task_id": a.id, "kind": "relates", "other_task_id": b.id}
    )
    assert not err and f"#{a.id} relates #{b.id}" in text
    text, err = await call_tool(services, ctx, "kanban_attach", {"task_id": b.id, "ref": "https://x.test/a"})
    assert not err and "attached https://x.test/a" in text


async def test_kanban_unblock_resets_and_events_use_spec_names(services: Services) -> None:
    kb = kb_of(services)
    t = await kb.create("b", "flaky")
    await kb.block(t.id, "boom 1")
    await kb.block(t.id, "boom 2")
    assert (await kb.get(t.id)).status == "blocked"
    parent = await new_parent(services)
    text, err = await call_tool(
        services, tool_ctx(services, parent), "kanban_unblock", {"task_id": t.id, "note": "fixed"}
    )
    assert not err and "unblocked" in text
    row = await kb.get(t.id)
    assert row.status == "ready" and row.failures == 0
    with pytest.raises(KanbanError, match="expected blocked"):
        await kb.unblock(t.id)
    await kb.complete(t.id)
    kinds = [e.type for e in await services.events.replay(0, frozenset({"server"}))]
    assert "kanban.blocked" in kinds and "kanban.done" in kinds and "kanban.unblocked" in kinds
    assert "kanban.task_done" not in kinds and "kanban.task_blocked" not in kinds
