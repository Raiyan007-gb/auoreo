from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import MessageArchiveRow, MessageRow
from auoreo.core.services import Services
from auoreo.core.settings import LoopSettings, ModelGroup, Settings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.gateway import LiteLLMGateway
from auoreo.runtime.compaction import (
    PRUNE_HEAD,
    PRUNE_TAIL,
    CompactionError,
    prune_plan,
    render_transcript,
    split_points,
    thresholds,
    turn_index,
)
from auoreo.schema.events import ServerEvent

FILLER = "lorem ipsum dolor sit amet " * 11  # ~300 chars per message


async def turns(svc: Services, sid: str, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        await svc.coordinator.submit(sid, f"turn {i}: {FILLER}", interactive=False)
        await svc.coordinator.wait(sid, 10)


async def since(svc: Services, sid: str, mark: int) -> list[ServerEvent]:
    """Durable events after `mark` (the bus may shed a slow test subscriber)."""
    events = await svc.events.replay(mark, frozenset({f"session:{sid}"}), limit=100_000)
    return [e for e in events if e.type not in {"message.delta", "usage"}]


async def db_messages(svc: Services, sid: str) -> list[MessageRow]:
    async with svc.db.session() as s:
        q = select(MessageRow).where(MessageRow.session_id == sid).order_by(MessageRow.idx)
        return list((await s.execute(q)).scalars())


# ------------------------------------------------------------------ S0 meter
@pytest.mark.parametrize(
    ("window", "max_out", "ratio", "expected"),
    [
        (200_000, None, None, (191_808, 178_808, 188_808)),
        (128_000, None, None, (119_808, 106_808, 116_808)),
        (200_000, 64_000, None, (180_000, 167_000, 177_000)),
        (1_000_000, 32_000, None, (980_000, 967_000, 977_000)),
        (200_000, None, 0.8, (191_808, 160_000, 188_808)),
        (6_000, None, None, (4_500, 3_750, 4_125)),  # tiny windows keep positive thresholds
    ],
)
def test_meter_thresholds(
    window: int, max_out: int | None, ratio: float | None, expected: tuple[int, int, int]
) -> None:
    th = thresholds(window, max_out, ratio)
    assert (th.effective, th.auto, th.blocking) == expected
    assert th.auto < th.blocking < th.effective < th.window


def test_threshold_ratio_only_applies_when_set(services: Services) -> None:
    comp = services.coordinator.runner.compaction
    assert comp.thresholds("default").auto == 106_808
    services.settings.loop = LoopSettings(compaction_threshold_ratio=0.5)
    assert comp.thresholds("default").auto == 64_000


def test_litellm_context_window_lookup(home: Any) -> None:
    st = Settings(
        home=home,
        models={
            "default": ModelGroup(models=["openai/gpt-4o-mini"]),
            "big": ModelGroup(models=["anthropic/claude-sonnet-4-5[1m]"]),
            "pinned": ModelGroup(models=["openai/some-local-model"], max_input_tokens=50_000),
        },
        fallbacks={},
    )
    gw = LiteLLMGateway(st, lambda _: None)
    assert gw.context_window("gpt-4o-mini") == 128_000
    assert gw.context_window("default") == 128_000
    assert gw.context_window("big") == 1_000_000
    assert gw.context_window("claude-opus[1m]") == 1_000_000
    assert gw.context_window("pinned") == 50_000
    assert gw.context_window("nobody/knows-this-model") == 128_000
    assert (gw.price("gpt-4o-mini", 1000, 100) or 0) > 0
    assert gw.price("nobody/knows-this-model", 1000, 100) is None


# ------------------------------------------------------------------ S2 prune
def _tool_pair(i: int, name: str, size: int) -> list[dict[str, Any]]:
    call = {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}
    return [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * (size - 10) + "END-MARKER"},
    ]


def test_prune_plan_shapes_old_read_results_only() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    hist += _tool_pair(1, "read", 30_000)  # old + prunable
    hist += _tool_pair(2, "write", 30_000)  # not a read-type tool
    hist += _tool_pair(3, "bash", 5_000)  # too small
    hist += [{"role": "user", "content": f"m{i}"} for i in range(10)]
    hist += _tool_pair(4, "grep", 30_000)  # within the last 10 messages
    hist += [{"role": "user", "content": f"n{i}"} for i in range(8)]
    changes, reclaimed = prune_plan(hist)
    assert list(changes) == [2]
    new = changes[2]["content"]
    assert new.startswith("x" * PRUNE_HEAD) and new.endswith("END-MARKER")
    assert f"[pruned {30_000 - PRUNE_HEAD - PRUNE_TAIL} chars]" in new
    assert changes[2]["tool_call_id"] == "c1" and reclaimed > 20_000
    assert "tool_calls" in hist[1]  # assistant side untouched


async def test_prune_hysteresis_and_persistence(services: Services) -> None:
    comp = services.coordinator.runner.compaction
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    runner = services.coordinator.runner
    small = [{"role": "user", "content": "go"}, *_tool_pair(1, "read", 9_000)]
    small += [{"role": "user", "content": f"m{i}"} for i in range(10)]
    for m in small:
        await runner._persist(rt, m)
    assert await comp._prune(rt) == 0  # ~3.9k chars reclaimable < 4096 tokens: skip
    for m in [*_tool_pair(2, "read", 40_000), *[{"role": "user", "content": f"k{i}"} for i in range(10)]]:
        await runner._persist(rt, m)
    assert await comp._prune(rt) == 2
    rows = await db_messages(services, sess.id)
    assert [r.message for r in rows] == rt.history
    assert "[pruned" in rt.history[2]["content"] and "[pruned" in rt.history[14]["content"]


# --------------------------------------------------------------- S6 split
def test_split_never_breaks_tool_pairs_and_keeps_a_real_user() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": "first"}]
    for i in range(12):
        hist.append({"role": "user", "content": f"u{i}"})
        hist += _tool_pair(i, "read", 100)
        hist.append({"role": "assistant", "content": f"a{i}"})
    sizes = [10] * len(hist)
    head, tail = split_points(hist, sizes, 0.16)
    assert head == 1 and hist[tail]["role"] == "user" and len(hist) - tail >= 20
    head, tail = split_points(hist, sizes, 0)
    assert hist[tail]["content"] == "u11"  # retain=0: only the last real user turn
    summary = {"role": "user", "content": "<compaction-summary>x</compaction-summary>"}
    assert split_points([hist[0], summary, *hist[-4:]], [1] * 6, 0) == (1, 2)


def test_transcript_drops_oldest_first() -> None:
    msgs = [{"role": "user", "content": f"{i}:" + "y" * 100} for i in range(50)]
    text = render_transcript(msgs, cap=1_000)
    assert len(text) < 1_200 and "49:" in text and "0:" not in text.split("\n")[1]
    assert text.startswith("[... ")


def test_turn_index_is_found_from_the_end() -> None:
    turn = {"role": "user", "content": "now"}
    hist = [{"role": "user", "content": "a"}, turn, {"role": "assistant", "content": "b"}]
    assert turn_index(hist, turn) == 1
    assert turn_index(hist[:1] + hist[2:], turn) == 0  # folded: fall back to last real user


# ------------------------------------------------------------ S4–S6 end-to-end
async def test_manual_compaction_shrinks_archives_and_keeps_system_prompt(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    rt = await services.coordinator.runtime(sess.id)
    hist_obj = rt.history
    before = list(rt.history)
    mark = await services.events.last_seq()
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.post_tokens < res.pre_tokens and res.archived > 0
    assert res.compaction_id.startswith("cmp_") and len(res.compaction_id) == 16
    assert rt.history is hist_obj
    assert rt.history[0] == before[0]
    assert rt.history[1]["content"].startswith('<compaction-summary trigger="manual"')
    assert rt.history[2:] == before[-(len(rt.history) - 2) :]
    assert len(rt.history) - 2 >= 20
    # messages table rewritten contiguously; archive holds exactly the replaced rows
    rows = await db_messages(services, sess.id)
    assert [r.idx for r in rows] == list(range(len(rt.history)))
    assert [r.message for r in rows] == rt.history
    async with services.db.session() as s:
        arch = list((await s.execute(select(MessageArchiveRow))).scalars())
    assert [a.message for a in arch] == before[1 : 1 + res.archived]
    assert {a.compaction_id for a in arch} == {res.compaction_id}
    kinds = [e.type for e in await since(services, sess.id, mark)]
    assert kinds == ["compaction.started", "compaction.completed"]
    # summarizer call: tool-free, small group, temperature 0, compaction template
    aux = next(r for r in reversed(gateway.requests) if r.metadata.get("purpose") == "compaction")
    assert aux.model == "small" and aux.tools == () and aux.temperature == 0
    assert aux.tool_choice == "none" and "## Next step" in aux.messages[0]["content"]
    # next turn: byte-identical system prompt, compaction text not in it
    await turns(services, sess.id, 1, start=99)
    main = [r for r in gateway.requests if r.metadata.get("purpose") not in {"compaction", "title"}]
    assert len({r.messages[0]["content"] for r in main}) == 1
    assert "compacting the middle" not in main[-1].messages[0]["content"]
    assert any("<compaction-summary" in str(m.get("content")) for m in main[-1].messages)


async def test_pressure_trigger_compacts_and_context_follows_turn(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.context_window_tokens = 6_000
    sess = await services.create_session()
    mark = await services.events.last_seq()
    await turns(services, sess.id, 20)
    events = await since(services, sess.id, mark)
    done = [e for e in events if e.type == "compaction.completed"]
    assert done and all(e.data["trigger"] == "pressure" for e in done)
    assert all(e.data["post_tokens"] < e.data["pre_tokens"] for e in done)
    assert not [e for e in events if e.type in {"compaction.failed", "run.failed"}]
    assert [e for e in events if e.type == "run.completed"].__len__() == 20
    last = [r for r in gateway.requests if r.metadata.get("purpose") != "compaction"][-1]
    users = [m for m in last.messages if m["role"] == "user"]
    assert users[-1]["content"].startswith("<context>") and "turn 19" in users[-1]["content"]
    assert sum("<context>" in str(m["content"]) for m in last.messages) == 1


async def test_overflow_compacts_and_retries_once(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 3)
    gateway.queue(Scripted(error=GatewayError("CONTEXT", "too long")), Scripted(text="recovered"))
    mark = await services.events.last_seq()
    rid = await services.coordinator.submit(sess.id, "big one", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed" and result.text == "recovered"
    done = [e for e in await since(services, sess.id, mark) if e.type == "compaction.completed"]
    assert len(done) == 1 and done[0].data["trigger"] == "overflow"
    rt = await services.coordinator.runtime(sess.id)
    assert rt.history[2]["content"] == "big one"  # retain=0: tail starts at this turn

    # a second CONTEXT after the compaction retry fails the turn (no second retry)
    gateway.queue(*[Scripted(error=GatewayError("CONTEXT", "still too long")) for _ in range(2)])
    rid = await services.coordinator.submit(sess.id, "again", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "failed" and result.error
    assert result.error["code"] == "CONTEXT"


async def test_circuit_breaker_trips_after_three_failures(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    comp = services.coordinator.runner.compaction
    gateway.compaction_script.extend(Scripted(error=GatewayError("SERVER", "aux down")) for _ in range(3))
    for attempt in range(3):
        with pytest.raises(CompactionError) as info:
            await comp.compact(sess.id, trigger="manual")
        assert info.value.tripped == (attempt == 2)
    assert comp.tripped(sess.id)
    # tripped: the pressure trigger no longer compacts, turns still run
    gateway.context_window_tokens = 2_000
    await turns(services, sess.id, 1, start=50)
    rt = await services.coordinator.runtime(sess.id)
    assert not any("<compaction-summary" in str(m.get("content")) for m in rt.history)
    # a successful manual compaction resets the breaker
    await comp.compact(sess.id, trigger="manual")
    assert not comp.tripped(sess.id)


async def test_breaker_trip_inside_a_turn_fails_with_compaction(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    comp = services.coordinator.runner.compaction
    comp.failures[sess.id] = 2
    gateway.context_window_tokens = 2_000
    gateway.compaction_script.append(Scripted(error=GatewayError("SERVER", "aux down")))
    mark = await services.events.last_seq()
    rid = await services.coordinator.submit(sess.id, "more", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "failed" and result.error
    assert result.error["code"] == "COMPACTION"
    kinds = [e.type for e in await since(services, sess.id, mark)]
    assert "compaction.failed" in kinds and kinds[-1] == "run.failed"


async def test_disabled_compaction_never_runs(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.compaction_enabled = False
    gateway.context_window_tokens = 2_000
    sess = await services.create_session()
    await turns(services, sess.id, 3)
    assert not [r for r in gateway.requests if r.metadata.get("purpose") == "compaction"]


# ------------------------------------------------------------------- API
@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_compact_and_archive_endpoints(client: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session()
    assert (await client.post(f"/api/sessions/{sess.id}/compact")).status_code == 422  # nothing yet
    await turns(services, sess.id, 15)
    r = await client.post(f"/api/sessions/{sess.id}/compact", json={"retain_ratio": 0.1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["post_tokens"] < body["pre_tokens"] and body["archived"] > 0
    await turns(services, sess.id, 12, start=20)
    second = (await client.post(f"/api/sessions/{sess.id}/compact")).json()
    arch = (await client.get(f"/api/sessions/{sess.id}/archive")).json()
    assert arch[0]["compaction_id"] == second["compaction_id"]  # newest compaction first
    assert arch[-1]["compaction_id"] == body["compaction_id"]
    assert len(arch) == body["archived"] + second["archived"]
    assert (await client.post("/api/sessions/ses_missing/compact")).status_code == 404
    assert (await client.get("/api/sessions/ses_missing/archive")).status_code == 404


async def test_compact_endpoint_conflicts_while_running(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = await services.create_session()
    monkeypatch.setattr(services.coordinator, "is_running", lambda sid: True)
    assert (await client.post(f"/api/sessions/{sess.id}/compact")).status_code == 409


async def test_nothing_to_compact_is_benign(services: Services, gateway: FakeGateway) -> None:
    """A short session: /compact is a no-op that never counts toward the circuit breaker."""
    from auoreo.runtime.compaction import NothingToCompact

    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "hello", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    comp = services.coordinator.runner.compaction
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    for _ in range(4):
        with pytest.raises(NothingToCompact):
            await comp.compact(sess.id, trigger="manual")
    assert comp.failures.get(sess.id, 0) == 0
    kinds = []
    while not sub.queue.empty():
        kinds.append(sub.queue.get_nowait().type)
    assert kinds.count("compaction.skipped") == 4 and "compaction.failed" not in kinds
    # the slash command swallows it: no exception, no run.failed
    await services.coordinator.submit(sess.id, "/compact", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    kinds = []
    while not sub.queue.empty():
        kinds.append(sub.queue.get_nowait().type)
    assert "compaction.skipped" in kinds and "run.failed" not in kinds
