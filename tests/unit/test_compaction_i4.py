"""I4 §11.9 follow-ups: S0 meter knobs, S2 skill prune, hygiene, S5 extras, S7 notes/marks."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.core.settings_i4 import CompactionI4Settings, ModelCompactionOverride
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.compaction import CompactionError
from auoreo.runtime.compaction_extras import (
    END_MARKER,
    HANDOFF,
    USER_EACH_CHARS,
    assemble,
    prior_checkpoint,
    strip_analysis,
)
from auoreo.runtime.compaction_i4 import PostCompaction, hygiene_due, meter, skill_prune_plan
from auoreo.runtime.compaction_restore import session_memory_path
from auoreo.runtime.session_memory import append_notes
from tests.unit.test_compaction_g2b import FILLER, aux_calls, call, since, turns


# ------------------------------------------------------------------ S0 meter
def test_meter_absolute_overrides_profile_floor_and_warn() -> None:
    cfg = CompactionI4Settings()
    base = meter(cfg, 200_000, "m", 8192, None)
    assert base.auto == 200_000 - 8192 - 13_000 and base.warn == base.auto - 20_000
    cfg = CompactionI4Settings(threshold_tokens=50_000)
    assert meter(cfg, 200_000, "m", 8192, None).auto == 50_000  # absolute fires first
    assert meter(cfg, 200_000, "m", 8192, 0.1).auto == 20_000  # ratio fires first
    cfg = CompactionI4Settings(
        model_overrides={
            "claude": ModelCompactionOverride(threshold_ratio=0.5),
            "claude-opus": ModelCompactionOverride(threshold_tokens=30_000),
        }
    )
    assert meter(cfg, 200_000, "anthropic/claude-opus-5", None, None).auto == 30_000  # longest wins
    assert meter(cfg, 200_000, "anthropic/claude-sonnet", None, None).auto == 100_000
    cfg = CompactionI4Settings(profile_threshold_ratio=0.5)
    assert meter(cfg, 200_000, "m", None, None).auto == 150_000  # floor 0.75 under 512K
    assert meter(cfg, 1_000_000, "m", None, None).auto == 500_000
    assert meter(cfg, 200_000, "m", None, 0.4).auto == 80_000  # explicit ratio: no floor
    assert hygiene_due(cfg, 1000, 850) and not hygiene_due(cfg, 1000, 849)


# --------------------------------------------------------------- S2 skills
def test_skill_prune_plan_markers_cap_and_recent() -> None:
    body = '<skill name="research">\n' + "x" * 6000 + "\n</skill>"
    small = '<skill name="tiny">\nshort\n</skill>'
    hist: list[dict[str, Any]] = [{"role": "user", "content": body} for _ in range(5)]
    hist += [{"role": "user", "content": small}] + [{"role": "user", "content": body} for _ in range(10)]
    changes, reclaimed = skill_prune_plan(hist, 5000, 3)
    assert sorted(changes) == [0, 1, 2] and reclaimed > 15_000  # cap 3, last 10 untouched
    assert changes[0]["content"] == "[SKILL_PRUNED: reload with skill(name='research')]"
    changes, _ = skill_prune_plan(hist, 5000, 20)
    assert 5 not in changes and max(changes) < len(hist) - 10


async def test_skill_bodies_are_pruned_during_compaction(services: Services) -> None:
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    runner = services.coordinator.runner
    await runner._persist(rt, {"role": "user", "content": "start"})
    for _ in range(3):
        await runner._persist(
            rt, {"role": "user", "content": '<skill name="big">\n' + "y" * 7000 + "\n</skill>"}
        )
    for i in range(12):
        await runner._persist(rt, {"role": "user", "content": f"tail {i}"})
    res = await runner.compaction.compact(sess.id, trigger="pressure", stop_below=10**9)
    assert res.source == "prune" and res.pruned == 3
    assert rt.history[1]["content"].startswith("[SKILL_PRUNED")


# ------------------------------------------------------------- S5 extras
def test_strip_analysis_and_prior_checkpoint() -> None:
    assert strip_analysis("<analysis>think</analysis>\n## Objective\n- x") == "## Objective\n- x"
    assert strip_analysis("<analysis>never closed\n## Objective\n- y") == "## Objective\n- y"
    middle = [
        {
            "role": "user",
            "content": '<compaction-summary trigger="manual" id="c1">\nOLD FACTS\n</compaction-summary>',
        },
        {"role": "user", "content": "new"},
    ]
    assert prior_checkpoint(middle) == "OLD FACTS"
    assert prior_checkpoint([{"role": "user", "content": "x"}]) is None


def test_assemble_sections_and_caps() -> None:
    middle: list[dict[str, Any]] = [{"role": "user", "content": f"ask {i} " + "z" * 5000} for i in range(8)]
    middle += [
        call(1, "read", {"path": "a.py"}),
        {"role": "tool", "tool_call_id": "c1", "content": "ok body"},
    ]
    text = assemble("<analysis>scratch</analysis>\n## Objective\n- goal", middle, "ses_1", 3, full=True)
    assert text.startswith(HANDOFF) and text.endswith(END_MARKER) and "scratch" not in text
    users = text.split("## User messages (verbatim, newest first)\n", 1)[1].split("\n## ", 1)[0]
    assert users.startswith("> ask 7") and len(users) <= 24_000 + 200  # newest first, 24K total
    assert all(len(b) <= USER_EACH_CHARS + 40 for b in users.split("\n\n"))  # 4K each
    assert "- read(a.py) → ok" in text and "- #3 user: ask 0" in text
    assert 'session_search(query="…", session_id="ses_1")' in text
    lean = assemble("## Objective\n- goal", middle, "ses_1", 3, full=False)
    assert "## Session log" not in lean and END_MARKER in lean


async def test_summary_request_merges_prior_checkpoint(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    comp = services.coordinator.runner.compaction
    await comp.compact(sess.id, trigger="manual", retain_ratio=0.1)
    await turns(services, sess.id, 15, start=30)
    await comp.compact(sess.id, trigger="manual", retain_ratio=0.1)
    second = aux_calls(gateway)[-1]
    joined = "\n".join(str(m.get("content")) for m in second.messages)
    assert "<previous-checkpoint>" in joined and "the conversation wins" in joined


# ------------------------------------------------ events, marks, guards
async def test_completed_event_fields_and_post_compaction_mark(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    notified: list[str] = []

    async def listener(sid: str, info: dict[str, Any]) -> None:
        notified.append(info["compaction_id"])

    comp = services.coordinator.runner.compaction
    comp.post.subscribe(listener)
    mark = await services.events.last_seq()
    res = await comp.compact(sess.id, trigger="manual")
    done = [e for e in await since(services, sess.id, mark) if e.type == "compaction.completed"][0].data
    for key in ("threshold", "pre_tokens", "post_tokens", "cache_read", "cache_creation", "summary_model"):
        assert key in done
    assert done["summary_model"] and done["failure_class"] is None and notified == [res.compaction_id]
    assert comp.post.marks(sess.id) == {"post_compaction": res.compaction_id}
    before = len(gateway.requests)
    await turns(services, sess.id, 1, start=99)
    main = [r for r in gateway.requests[before:] if not r.metadata.get("purpose")]
    assert main[0].metadata.get("post_compaction") == res.compaction_id
    assert comp.post.marks(sess.id) == {}  # cleared once the assistant message committed


def test_post_compaction_listener_errors_are_contained() -> None:
    post = PostCompaction()

    async def boom(sid: str, info: dict[str, Any]) -> None:
        raise RuntimeError("x")

    post.subscribe(boom)
    asyncio.run(post.notify_compaction("s", {}))


async def test_subagent_guard_and_hygiene_trigger(services: Services, gateway: FakeGateway) -> None:
    gateway.context_window_tokens = 4_000
    services.settings.loop.compaction_threshold_ratio = 1.0  # auto threshold effectively off
    services.settings.i4.compaction.hygiene_ratio = 0.3
    child = await services.create_session(parent_id=(await services.create_session()).id)
    mark = await services.events.last_seq()
    await turns(services, child.id, 8)
    assert not [e for e in await since(services, child.id, mark) if e.type == "compaction.started"]
    sess = await services.create_session()
    mark = await services.events.last_seq()
    await turns(services, sess.id, 8)
    started = [e for e in await since(services, sess.id, mark) if e.type == "compaction.started"]
    assert started and started[0].data["trigger"] == "hygiene"


async def test_circuit_breaker_reason_has_doctor_hint(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    comp = services.coordinator.runner.compaction
    gateway.compaction_script.extend(Scripted(error=GatewayError("INTERNAL", "boom")) for _ in range(3))
    for _ in range(3):
        with pytest.raises(CompactionError) as info:
            await comp.compact(sess.id, trigger="manual")
    assert info.value.tripped and "auoreo doctor" in info.value.reason


async def test_idle_turn_hold_waits_for_a_needed_compaction(services: Services, gateway: FakeGateway) -> None:
    services.settings.compaction.idle_compact_after_seconds = 0.05
    services.settings.compaction.idle_min_tokens = 0
    services.settings.i4.compaction.hygiene_ratio = 0.01
    services.settings.i4.compaction.hygiene_hold_s = 5
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    gateway.compaction_script.append(Scripted(text="## Objective\n- held", delay_s=0.3))
    await asyncio.sleep(0.15)  # idle compaction is summarizing now
    mark = await services.events.last_seq()
    await turns(services, sess.id, 1, start=60)
    types = [e.type for e in await since(services, sess.id, mark)]
    assert "compaction.completed" in types and types.index("compaction.completed") < types.index(
        "run.started"
    )


# ------------------------------------------------------- S7 session memory
async def test_session_memory_writer_feeds_session_memory_compaction(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    for i in range(20):
        await services.coordinator.submit(sess.id, f"turn {i}: {FILLER * 6}", interactive=False)
        await services.coordinator.wait(sess.id, 10)
    path = session_memory_path(services.settings.home, sess.id)
    notes = path.read_text()
    assert (
        notes.startswith("# Session memory")
        and notes.count("\n## Turn ") >= 10
        and "## Turn 20 " in notes
        and "turn 19" in notes
    )
    services.settings.compaction.session_memory_min_tokens = 200
    calls = len(aux_calls(gateway))
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.source == "session_memory" and len(aux_calls(gateway)) == calls


def test_append_notes_drops_oldest_turns(tmp_path: Any) -> None:
    path = tmp_path / "s" / "session-memory.md"
    for i in range(30):
        text = append_notes(path, f"## Turn {i} — t\n- User: {FILLER}", 2_000)
    assert len(text) <= 2_000 and "## Turn 29" in text and "## Turn 0 " not in text
