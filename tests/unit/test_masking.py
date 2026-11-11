"""Observation masking as a compaction source (M7 W1 Wave 2, `m7.token.masking = "compaction"`).

Every compaction run first replaces old bulky tool results by a placeholder, after spilling
each original under `sessions/<sid>/tool-results/masked/`. `/compact unmask=<id|all>` restores.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import MessageRow
from auoreo.core.services import Services
from auoreo.core.settings import ModelGroup
from auoreo.gateway.fake import FakeGateway
from auoreo.runtime import masking
from auoreo.runtime.commands import compact_args
from auoreo.runtime.compaction import CompactionError
from auoreo.runtime.masking_plan import MASK_PREFIX, NOTICE_MASKED, spill_name
from auoreo.schema.events import ServerEvent
from auoreo.tools.read_dedupe import NOTICE_PREFIX

BIG = 12_000


def pair(call_id: str, name: str, content: str) -> list[dict[str, Any]]:
    call = {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
    return [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def body(tag: str, size: int = BIG) -> str:
    line = f"{tag} line with a newline\r\n and unicode é\n"
    return (line * (size // len(line) + 1))[:size]


async def seeded(
    svc: Services, *, ids: tuple[str, ...] = ("c1", "c2"), notice: bool = False, size: int = BIG
) -> Any:
    """A session whose history holds old, big `read` results plus 10 recent small ones."""
    sess = await svc.create_session()
    rt = await svc.coordinator.runtime(sess.id)
    msgs: list[dict[str, Any]] = [{"role": "user", "content": c} for c in ("a", "b", "go")]
    for cid in ids:
        msgs += pair(cid, "read", body(cid, size))
    if notice:
        msgs += pair("n1", "read", f"{NOTICE_PREFIX} /x (offset 0, limit 2000) has not changed")
    for i in range(10):
        msgs += pair(f"r{i}", "bash", "ok")
    for m in msgs:
        await svc.coordinator.runner._persist(rt, m)
    return rt


def enable(svc: Services) -> None:
    svc.settings.m7.token.masking = "compaction"


async def rows(svc: Services, sid: str) -> list[dict[str, Any]]:
    async with svc.db.session() as s:
        q = select(MessageRow).where(MessageRow.session_id == sid).order_by(MessageRow.idx)
        return [r.message for r in (await s.execute(q)).scalars()]


async def events(svc: Services, sid: str, kind: str) -> list[ServerEvent]:
    found = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [e for e in found if e.type == kind]


def spill(svc: Services, sid: str, call_id: str) -> Path:
    return svc.settings.home / "sessions" / sid / "tool-results" / "masked" / f"{spill_name(call_id)}.txt"


async def compact(svc: Services, rt: Any, stop_below: int | None = 10**9) -> Any:
    comp = svc.coordinator.runner.compaction
    return await comp.compact(
        rt.session_id, trigger="pressure", rt=rt, model="default", stop_below=stop_below
    )


async def test_masking_completes_a_compaction_without_a_summary(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    originals = {m["tool_call_id"]: m["content"] for m in rt.history if m.get("role") == "tool"}
    res = await compact(services, rt)
    assert (res.source, res.pruned, res.archived) == ("mask", 2, 0)
    for cid in ("c1", "c2"):
        assert spill(services, rt.session_id, cid).read_bytes().decode() == originals[cid]
    masked = [m for m in rt.history if str(m.get("content", "")).startswith(MASK_PREFIX)]
    assert [m["tool_call_id"] for m in masked] == ["c1", "c2"]
    text = masked[0]["content"]
    assert "read result of call c1" in text and str(spill(services, rt.session_id, "c1")) in text
    assert f"{BIG} chars" in text and set(masked[0]) == {"role", "tool_call_id", "content"}
    assert await rows(services, rt.session_id) == rt.history
    assert rt.history[-1]["content"] == "ok"  # the recent tail is untouched


async def test_context_masked_event_and_post_compaction_mark(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    res = await compact(services, rt)
    (ev,) = await events(services, rt.session_id, "context.masked")
    data = ev.data
    assert data["compaction_id"] == res.compaction_id and data["trigger"] == "pressure"
    assert data["call_ids"] == ["c1", "c2"] and data["count"] == 2 and data["gate"] == "pressure"
    assert data["tokens"] > 0 and data["suffix_tokens"] > 0 and "hit_ratio" in data
    assert "content" not in data and not any(str(services.settings.home) in str(v) for v in data.values())
    (done,) = await events(services, rt.session_id, "compaction.completed")
    assert done.data["source"] == "mask"
    comp = services.coordinator.runner.compaction
    assert comp.post.marks(rt.session_id)  # the next request is post-compaction: no prefix warning


async def test_flag_off_masks_nothing(services: Services) -> None:
    rt = await seeded(services)
    with contextlib.suppress(CompactionError):  # today's path: prune, then the summarizer
        await compact(services, rt)
    assert not any(str(m.get("content", "")).startswith(MASK_PREFIX) for m in rt.history)
    assert not (services.settings.home / "sessions" / rt.session_id / "tool-results" / "masked").exists()
    assert await events(services, rt.session_id, "context.masked") == []


async def test_spill_is_written_before_any_row_changes(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(services)
    rt = await seeded(services)
    comp = services.coordinator.runner.compaction
    real = comp._rows
    seen: list[bool] = []

    async def rows_spy(s: Any, rt_: Any) -> Any:
        seen.append(all(spill(services, rt.session_id, c).exists() for c in ("c1", "c2")))
        return await real(s, rt_)

    monkeypatch.setattr(comp, "_rows", rows_spy)
    await compact(services, rt)
    assert seen and seen[0] is True


async def test_a_failed_spill_leaves_that_message_alone(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(services)
    rt = await seeded(services)
    real = masking.write_spill

    def flaky(path: Path, text: str) -> bool:
        return False if path.stem == "c1" else real(path, text)

    monkeypatch.setattr(masking, "write_spill", flaky)
    await compact(services, rt)
    by_id = {m.get("tool_call_id"): m["content"] for m in rt.history if m.get("role") == "tool"}
    assert by_id["c1"] == body("c1") and by_id["c2"].startswith(MASK_PREFIX)


async def test_an_unsafe_call_id_spills_under_a_hash(services: Services) -> None:
    enable(services)
    rt = await seeded(services, ids=("CON", "a/../b:c"))
    await compact(services, rt)
    folder = services.settings.home / "sessions" / rt.session_id / "tool-results" / "masked"
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2 and all(n.startswith("h_") and n.endswith(".txt") for n in names)
    assert all(p.parent == folder for p in folder.iterdir())


async def test_the_executor_spill_is_named_when_it_exists(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    full = services.settings.home / "sessions" / rt.session_id / "tool-results" / "c1.txt"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("the untruncated output", encoding="utf-8")
    await compact(services, rt)
    c1 = next(m for m in rt.history if m.get("tool_call_id") == "c1")
    assert f"Original: {full}." in c1["content"]
    assert spill(services, rt.session_id, "c1").exists()  # what the history held, for unmask


async def test_stale_read_notice_is_rewritten_in_the_same_batch(services: Services) -> None:
    enable(services)
    rt = await seeded(services, notice=True)
    await compact(services, rt)
    n1 = next(m for m in rt.history if m.get("tool_call_id") == "n1")
    assert n1["content"] == NOTICE_MASKED
    assert await rows(services, rt.session_id) == rt.history


async def test_not_below_stop_below_continues_to_summary(services: Services, gateway: FakeGateway) -> None:
    enable(services)
    rt = await seeded(services)
    res = await compact(services, rt, stop_below=1)
    assert res.source in {"summary", "session_memory"}
    assert len(await events(services, rt.session_id, "context.masked")) == 1


async def test_unmask_restores_bytes_and_emits_an_event(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    await compact(services, rt)
    comp = services.coordinator.runner.compaction
    assert await masking.unmask(comp, rt, "c1") == ["c1"]
    by_id = {m.get("tool_call_id"): m["content"] for m in rt.history if m.get("role") == "tool"}
    assert by_id["c1"] == body("c1") and by_id["c2"].startswith(MASK_PREFIX)
    assert await masking.unmask(comp, rt, "all") == ["c2"]
    assert await rows(services, rt.session_id) == rt.history
    evs = await events(services, rt.session_id, "context.unmasked")
    assert [e.data for e in evs] == [{"call_ids": ["c1"], "count": 1}, {"call_ids": ["c2"], "count": 1}]
    assert await masking.unmask(comp, rt, "all") == []  # nothing left


async def test_unmask_works_with_the_flag_off(services: Services) -> None:
    """Rollback: `/compact unmask=all` before switching back puts the originals back."""
    enable(services)
    rt = await seeded(services)
    await compact(services, rt)
    services.settings.m7.token.masking = "off"
    assert await masking.unmask(services.coordinator.runner.compaction, rt, "all") == ["c1", "c2"]


async def test_a_placeholder_without_a_spill_file_is_not_restored(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    await compact(services, rt)
    spill(services, rt.session_id, "c1").unlink()
    assert await masking.unmask(services.coordinator.runner.compaction, rt, "all") == ["c2"]


def test_compact_args_parse_unmask() -> None:
    assert compact_args("unmask=call_1") == {"unmask": "call_1"}
    assert compact_args("unmask=all retain=0.2") == {"unmask": "all", "retain_ratio": 0.2}


async def test_slash_compact_unmask_restores(services: Services) -> None:
    enable(services)
    rt = await seeded(services)
    await compact(services, rt)
    await services.coordinator.submit(rt.session_id, "/compact unmask=all", interactive=False)
    await services.coordinator.wait(rt.session_id, 10)
    assert not any(str(m.get("content", "")).startswith(MASK_PREFIX) for m in rt.history)
    assert len(await events(services, rt.session_id, "context.unmasked")) == 1


async def test_pressure_compaction_inside_a_turn_masks(services: Services, gateway: FakeGateway) -> None:
    """End to end: the auto threshold is reached and masking alone brings it below."""
    enable(services)
    services.settings.models["default"] = ModelGroup(models=["default"])
    rt = await seeded(services, size=40_000)
    gateway.context_window_tokens = 30_000
    comp = services.coordinator.runner.compaction
    th = comp.thresholds("default")
    assert comp.tokens("default", rt.history) > th.auto
    await services.coordinator.submit(rt.session_id, "continue", interactive=False)
    await services.coordinator.wait(rt.session_id, 10)
    (done,) = await events(services, rt.session_id, "compaction.completed")
    assert done.data["source"] == "mask" and done.data["trigger"] == "pressure"
