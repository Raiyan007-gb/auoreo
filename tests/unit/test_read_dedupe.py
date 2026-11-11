"""M7 step 1.7: a repeated, unchanged `read` in the same turn returns a short notice.

The notice is only correct while the earlier result is still in the model's context, so every
case that removes or alters it (a changed file, compaction, pruning, a new turn, a session
without a live history) must fall back to the full read.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.tools import read_dedupe
from auoreo.tools.context import CURRENT_CALL, CallInfo, ToolContext, ToolResult
from tests.conftest import REPO

BODY = "\n".join(f"line {i} of a file that is long enough to be worth deduplicating" for i in range(60))


def _handler() -> Any:
    spec = importlib.util.spec_from_file_location("read_handler_under_test", REPO / "tools/read/handler.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run


RUN = _handler()


class World:
    """A turn as the handler sees it: a live history, a per-turn context, numbered calls."""

    def __init__(self, tmp_path: Path, *, enabled: bool = True) -> None:
        self.turn_msg: dict[str, Any] = {"role": "user", "content": "go"}
        self.history: list[dict[str, Any]] = [self.turn_msg]
        st = Settings(home=tmp_path / "home", m7={"token": {"read_dedupe": enabled}})
        self.services = SimpleNamespace(
            settings=st, coordinator=SimpleNamespace(history_of=lambda sid: self.history)
        )
        self.cwd = tmp_path
        self.ctx = self.new_turn(self.turn_msg)
        self.n = 0

    def new_turn(self, turn_msg: dict[str, Any]) -> ToolContext:
        services = {"services": self.services, "turn_msg": turn_msg}
        return ToolContext("s1", self.cwd, self.cwd / "home", services=services)

    async def read(self, path: Path, *, persist: bool = True, **args: Any) -> str:
        """One `read` call; like the runner, its result becomes a tool message afterwards."""
        self.n += 1
        call_id = f"call_{self.n}"
        token = CURRENT_CALL.set(CallInfo(call_id, "read"))
        try:
            raw = await RUN({"path": str(path), **args}, self.ctx)
        finally:
            CURRENT_CALL.reset(token)
        text = raw.content if isinstance(raw, ToolResult) else str(raw)
        if persist:
            self.history.append({"role": "tool", "tool_call_id": call_id, "content": text})
        return text


def _file(tmp_path: Path, text: str = BODY) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _is_notice(text: str) -> bool:
    return text.startswith(read_dedupe.NOTICE_PREFIX)


async def test_second_identical_read_in_the_turn_is_a_short_notice(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    first = await w.read(path)
    second = await w.read(path)
    assert "line 59" in first and not _is_notice(first)
    assert _is_notice(second) and len(second) < 400 < len(first)
    assert str(path) in second and "line 59" not in second
    third = await w.read(path)  # the notice itself never becomes the reference
    assert _is_notice(third)


async def test_flag_off_returns_the_full_content_every_time(tmp_path: Path) -> None:
    w, path = World(tmp_path, enabled=False), _file(tmp_path)
    first = await w.read(path)
    assert await w.read(path) == first and not _is_notice(first)
    assert w.ctx.read_dedupe == {}


async def test_changed_file_is_read_in_full(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path)
    path.write_text(BODY.replace("line 7 ", "LINE SEVEN "), encoding="utf-8")
    changed = await w.read(path)
    assert not _is_notice(changed) and "LINE SEVEN" in changed
    assert _is_notice(await w.read(path))  # unchanged since the second, full read


async def test_content_that_changed_and_changed_back_is_read_in_full(tmp_path: Path) -> None:
    """A → B → A: the model saw B last, so "unchanged" would point at the wrong result."""
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path, offset=1, limit=10)
    path.write_text(BODY + "\nextra", encoding="utf-8")
    assert not _is_notice(await w.read(path, offset=20, limit=10))
    path.write_text(BODY, encoding="utf-8")
    assert not _is_notice(await w.read(path, offset=1, limit=10))


async def test_another_range_of_the_same_file_is_read_in_full(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path, offset=1, limit=20)
    other = await w.read(path, offset=21, limit=20)
    assert not _is_notice(other) and "line 25" in other
    assert _is_notice(await w.read(path, offset=1, limit=20))
    assert not _is_notice(await w.read(path))  # default range differs from 1..20


async def test_compaction_that_drops_the_earlier_read_restores_the_full_read(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path)
    summary = {"role": "user", "content": "<compaction-summary>…</compaction-summary>"}
    w.history[:] = [w.turn_msg, summary]  # `history[:] = new`, as CompactionService._commit does
    again = await w.read(path)
    assert not _is_notice(again) and "line 59" in again


async def test_pruned_earlier_read_restores_the_full_read(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path)
    old = w.history[1]
    w.history[1] = {**old, "content": old["content"][:50] + "\n… [pruned 999 chars] …\n"}  # `_prune`
    assert not _is_notice(await w.read(path))


async def test_result_altered_before_it_reached_the_history_is_not_trusted(tmp_path: Path) -> None:
    """Redaction or truncation in the executor: the model did not see what the handler returned."""
    w, path = World(tmp_path), _file(tmp_path)
    text = await w.read(path, persist=False)
    w.history.append({"role": "tool", "tool_call_id": "call_1", "content": text[:200] + "[truncated]"})
    assert not _is_notice(await w.read(path))


async def test_earlier_read_that_never_reached_the_history_is_not_trusted(tmp_path: Path) -> None:
    """Two identical reads of one parallel batch: the first result is not persisted yet."""
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path, persist=False)
    assert not _is_notice(await w.read(path))


async def test_a_new_turn_reads_in_full_even_if_the_state_leaked(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path)
    leaked = w.ctx.read_dedupe
    next_msg: dict[str, Any] = {"role": "user", "content": "next"}
    w.history.append(next_msg)
    w.ctx = w.new_turn(next_msg)
    assert w.ctx.read_dedupe == {}  # the state lives on the per-turn context
    w.ctx.read_dedupe = leaked  # even a leaked entry only counts after this turn's message
    assert not _is_notice(await w.read(path))


async def test_no_live_history_or_turn_message_means_no_dedupe(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    w.services.coordinator.history_of = lambda sid: None  # e.g. a tool run outside a session turn
    await w.read(path)
    assert not _is_notice(await w.read(path))
    w2 = World(tmp_path)
    w2.ctx.services.pop("turn_msg")
    await w2.read(path)
    assert not _is_notice(await w2.read(path))
    w3 = World(tmp_path)
    await w3.read(path)
    w3.history.remove(w3.turn_msg)  # the turn message itself was compacted away
    assert not _is_notice(await w3.read(path))


async def test_small_files_are_not_deduplicated(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path, "tiny\n")
    first = await w.read(path)
    assert await w.read(path) == first


async def test_read_before_edit_bookkeeping_is_kept_on_a_notice(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await w.read(path)
    w.ctx.state.read_files.clear()
    assert _is_notice(await w.read(path))
    assert w.ctx.state.read_files[str(path)] == path.stat().st_mtime


# ------------------------------------------------------------------ through the real runner
@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, m7={"token": {"read_dedupe": True}})


def _tool_results(svc: Services, sid: str) -> list[str]:
    history = svc.coordinator.history_of(sid)
    assert history is not None
    return [str(m["content"]) for m in history if m.get("role") == "tool"]


async def test_runner_dedupes_within_a_turn_and_not_across_turns(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    path = _file(workdir)
    read = ("read", {"path": str(path)})
    gateway.queue(
        Scripted(tool_calls=[read]),
        Scripted(tool_calls=[read]),
        Scripted(text="done"),
        Scripted(text="a title"),  # the background title call after the first turn
    )
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "read it twice", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    first, second = _tool_results(services, sess.id)
    assert "line 59" in first and _is_notice(second)
    for _ in range(200):  # let the title call take its scripted answer
        if not gateway.script:
            break
        await asyncio.sleep(0.02)
    gateway.queue(Scripted(tool_calls=[read]), Scripted(text="done again"))
    await services.coordinator.submit(sess.id, "and once more", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    assert "line 59" in _tool_results(services, sess.id)[2]  # a new turn: the full content


async def test_runner_reads_both_copies_of_a_parallel_batch_in_full(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    path = _file(workdir)
    read = ("read", {"path": str(path)})
    gateway.queue(Scripted(tool_calls=[read, read]), Scripted(text="done"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "read it twice at once", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    assert all("line 59" in r for r in _tool_results(services, sess.id))


# ------------------------------------------------- M7 Wave 2: observation masking
async def _read_with_call(w: World, path: Path) -> str:
    """A `read` preceded by its assistant tool call, so masking knows the tool name."""
    call = {"id": f"call_{w.n + 1}", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    w.history.append({"role": "assistant", "content": None, "tool_calls": [call]})
    return await w.read(path)


def _mask(w: World, tmp_path: Path) -> list[str]:
    """One masking batch over the live history, applied in place as the service does."""
    from auoreo.core.settings_m7 import M7Mask
    from auoreo.runtime.masking import mask_batch

    w.history[:0] = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]  # head
    batch = mask_batch(w.history, M7Mask(keep_recent=0), tmp_path / "masked", tmp_path / "spill")
    for i, m in batch.changes.items():
        w.history[i] = m
    return batch.call_ids


async def test_a_read_after_its_earlier_result_was_masked_is_read_in_full(tmp_path: Path) -> None:
    w, path = World(tmp_path), _file(tmp_path)
    await _read_with_call(w, path)
    assert _mask(w, tmp_path) == ["call_1"]
    again = await w.read(path)
    assert not _is_notice(again) and "line 59" in again


async def test_a_notice_whose_earlier_read_was_masked_is_rewritten(tmp_path: Path) -> None:
    from auoreo.runtime.masking_plan import NOTICE_MASKED

    w, path = World(tmp_path), _file(tmp_path)
    await _read_with_call(w, path)
    assert _is_notice(await _read_with_call(w, path))
    assert _mask(w, tmp_path) == ["call_1"]  # the notice itself is short: never masked as a result
    notice = next(m for m in w.history if m.get("tool_call_id") == "call_2")
    assert notice["content"] == NOTICE_MASKED
    assert not _is_notice(await w.read(path))  # and the next read is full again
