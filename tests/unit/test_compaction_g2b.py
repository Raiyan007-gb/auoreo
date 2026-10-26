"""G2b: compaction S4(c)/S4(d)/S5/S7 extensions, memory nudge and recall-query rewrite."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.core.settings_compaction import CompactionSettings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.commands import compact_args
from auoreo.runtime.compaction import CompactionError
from auoreo.runtime.compaction_plan import (
    TRUNCATED_MARKER,
    drop_oldest_group,
    partial_bounds,
    round_groups,
    session_memory_split,
)
from auoreo.runtime.compaction_restore import session_memory_path
from auoreo.runtime.nudges import NUDGE
from auoreo.schema.events import ServerEvent

FILLER = "lorem ipsum dolor sit amet " * 11


async def turns(svc: Services, sid: str, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        await svc.coordinator.submit(sid, f"turn {i}: {FILLER}", interactive=False)
        await svc.coordinator.wait(sid, 10)


async def since(svc: Services, sid: str, mark: int) -> list[ServerEvent]:
    events = await svc.events.replay(mark, frozenset({f"session:{sid}"}), limit=100_000)
    return [e for e in events if e.type not in {"message.delta", "usage"}]


def aux_calls(gateway: FakeGateway, purpose: str = "compaction") -> list[Any]:
    return [r for r in gateway.requests if r.metadata.get("purpose") == purpose]


def call(i: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = {"name": name, "arguments": json.dumps(args)}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": f"c{i}", "type": "function", "function": fn}],
    }


# ----------------------------------------------------------------- settings
def test_settings_defaults_and_voice_silence(settings: Settings) -> None:
    c = settings.compaction
    assert c.idle_compact_after_seconds is None and c.timeout_ladder_s == [60.0, 300.0, 900.0]
    assert c.memory_nudge_interval == 10 and c.rewrite_memory_query is False
    assert (c.restore_files, c.restore_file_chars, c.restore_files_total_chars) == (5, 5_000, 50_000)
    assert settings.voice_endpoint_silence_ms == 1250
    with pytest.raises(ValueError):
        CompactionSettings(timeout_ladder_s=[])


# ------------------------------------------------------------ pure helpers
def test_round_groups_and_ptl_drop() -> None:
    msgs = [{"role": "user", "content": "u0"}, call(1, "read", {}), {"role": "tool", "tool_call_id": "c1"}]
    msgs += [{"role": "assistant", "content": "a"}, {"role": "user", "content": "u1"}]
    assert [len(g) for g in round_groups(msgs)] == [1, 2, 1, 1]
    once = drop_oldest_group(msgs)
    assert once is not None and once[0] == {"role": "user", "content": TRUNCATED_MARKER}  # assistant-first
    assert once[1]["tool_calls"] and once[2]["role"] == "tool"  # the pair stays together
    twice = drop_oldest_group(once)
    assert twice is not None and twice[0]["content"] == TRUNCATED_MARKER and twice[1]["content"] == "a"
    assert drop_oldest_group([{"role": "user", "content": "only"}]) is None


def test_partial_bounds_protect_head_and_tool_pairs() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": "first"}]
    for i in range(6):
        hist += [
            {"role": "user", "content": f"u{i}"},
            call(i, "read", {}),
            {"role": "tool", "tool_call_id": f"c{i}"},
        ]
    assert partial_bounds(hist, 10, up_to=6, from_=None) == (1, 7)  # 6 is a tool result → moves on
    assert partial_bounds(hist, 10, up_to=None, from_=3) == (2, len(hist))  # from a tool → back
    assert partial_bounds(hist, 10, up_to=None, from_=0) == (1, len(hist))  # head stays
    assert partial_bounds(hist, 10, up_to=None, from_=None) == (1, 10)


def test_session_memory_split_keeps_min_tail() -> None:
    hist = [{"role": "user", "content": f"u{i}"} for i in range(30)]
    head, tail = session_memory_split(hist, [100] * 30, min_tokens=500, min_messages=5, max_tokens=4000)
    assert head == 1 and tail == 25
    head, tail = session_memory_split(hist, [100] * 30, min_tokens=500, min_messages=5, max_tokens=250)
    assert tail == 28  # the max cap wins


def test_compact_args() -> None:
    assert compact_args("up_to=12 from=3 retain=0.1") == {"up_to": 12, "from_": 3, "retain_ratio": 0.1}
    assert compact_args("up_to=x please") == {}


# ---------------------------------------------------------------- S5 retries
async def test_length_finish_aborts_and_preserves_history(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    rt = await services.coordinator.runtime(sess.id)
    before = list(rt.history)
    gateway.compaction_script.append(Scripted(text="## Objective\n- cut", finish="length"))
    mark = await services.events.last_seq()
    with pytest.raises(CompactionError) as info:
        await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert info.value.failure_class == "length" and "finish_reason=length" in info.value.reason
    assert rt.history == before
    failed = [e for e in await since(services, sess.id, mark) if e.type == "compaction.failed"]
    assert failed and failed[0].data["failure_class"] == "length"


async def test_prompt_too_long_retries_by_dropping_oldest_groups(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    gateway.compaction_script.extend(Scripted(error=GatewayError("CONTEXT", "too long")) for _ in range(2))
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.retries == 2 and res.archived > 0
    calls = aux_calls(gateway)[-3:]
    sizes = [len(c.messages[2]["content"]) for c in calls]
    assert sizes[0] > sizes[1] > sizes[2]  # each retry summarizes a shorter middle
    # a PTL that never resolves fails after ptl_max_retries
    services.settings.compaction.ptl_max_retries = 1
    await turns(services, sess.id, 12, start=50)
    gateway.compaction_script.extend(Scripted(error=GatewayError("CONTEXT", "too long")) for _ in range(2))
    with pytest.raises(CompactionError) as info:
        await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert info.value.failure_class == "prompt_too_long"


async def test_timeout_ladder_climbs_then_fails(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    services.settings.compaction.timeout_ladder_s = [0.05, 0.1, 5.0]
    gateway.compaction_script.extend([Scripted(text="slow", delay_s=0.3), Scripted(text="slow", delay_s=0.3)])
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.retries == 2 and res.source == "summary"
    await turns(services, sess.id, 12, start=50)
    services.settings.compaction.timeout_ladder_s = [0.05, 0.05]
    gateway.compaction_script.extend(Scripted(text="slow", delay_s=0.3) for _ in range(2))
    with pytest.raises(CompactionError) as info:
        await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert info.value.failure_class == "timeout" and "0.05s" in info.value.reason


# -------------------------------------------------------------- S7 restore
async def _seed(services: Services, workdir: Path, sid: str) -> None:
    (workdir / "src").mkdir(exist_ok=True)
    for i in range(7):
        (workdir / "src" / f"f{i}.py").write_text(f"# file {i}\n" + "x = 1\n" * 50)
    (workdir / ".env").write_text("SECRET=1\n")
    rt = await services.coordinator.runtime(sid)
    runner = services.coordinator.runner
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "start"}]
    for i in range(7):
        msgs += [
            {"role": "user", "content": f"look at f{i} {FILLER}"},
            call(i, "read", {"path": f"src/f{i}.py"}),
        ]
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"# file {i} {FILLER}"})
    msgs += [{"role": "user", "content": "secret"}, call(20, "read", {"path": ".env"})]
    msgs.append({"role": "tool", "tool_call_id": "c20", "content": "denied"})
    msgs += [{"role": "user", "content": "skill"}, call(21, "skill", {"name": "research"})]
    msgs.append({"role": "tool", "tool_call_id": "c21", "content": "RESEARCH SKILL BODY " + FILLER})
    msgs += [{"role": "user", "content": f"tail {i} {FILLER}"} for i in range(22)]
    for m in msgs:
        await runner._persist(rt, m)


async def test_post_compaction_restores_files_skills_metadata_and_reruns_session_start(
    services: Services, gateway: FakeGateway, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []

    class StubHooks:
        async def run(self, event: str, *, session_id: str, payload: dict[str, Any], match_key: str) -> None:
            ran.append((event, payload))

    monkeypatch.setattr("auoreo.runtime.compaction_restore.hooks_of", lambda s: StubHooks())
    sess = await services.create_session()
    await _seed(services, workdir, sess.id)
    rt = await services.coordinator.runtime(sess.id)
    rt.state.plan = [{"step": "do it", "status": "in_progress"}]
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    content = rt.history[1]["content"]
    assert res.source == "summary" and content.startswith('<compaction-summary trigger="manual"')
    restored = content.split("</compaction-summary>", 1)[1]
    assert "<restored-context>" in restored and f"session_id: {sess.id}" in restored
    files = [f"src/f{i}.py" for i in range(7) if f'<file path="src/f{i}.py">' in restored]
    assert files == [f"src/f{i}.py" for i in range(2, 7)]  # the 5 most recently touched
    assert '<file path=".env">' not in restored and "SECRET=1" not in restored  # secret floor
    assert '<skill name="research">' in restored and "RESEARCH SKILL BODY" in restored
    assert "<plan>" in restored and "do it" in restored
    assert [e for e, _ in ran] == ["SessionStart"] and ran[0][1]["source"] == "compact"


async def test_session_memory_compaction_skips_the_llm(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 20)
    path = session_memory_path(services.settings.home, sess.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Objective\n- notes kept by the session-memory writer\n")
    services.settings.compaction.session_memory_min_tokens = 200
    calls = len(aux_calls(gateway))
    mark = await services.events.last_seq()
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.source == "session_memory" and res.post_tokens < res.pre_tokens
    assert len(aux_calls(gateway)) == calls  # no summarizer call
    rt = await services.coordinator.runtime(sess.id)
    assert 'source="session_memory"' in rt.history[1]["content"] and "notes kept" in rt.history[1]["content"]
    done = [e for e in await since(services, sess.id, mark) if e.type == "compaction.completed"]
    assert done[0].data["source"] == "session_memory"
    # disabled → the LLM summarizer runs again
    services.settings.compaction.session_memory = False
    await turns(services, sess.id, 20, start=40)
    res = await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert res.source == "summary" and len(aux_calls(gateway)) == calls + 1


# ---------------------------------------------------------- S4(c) idle
async def test_idle_trigger_compacts_once_and_new_input_cancels(
    services: Services, gateway: FakeGateway
) -> None:
    services.settings.compaction.idle_compact_after_seconds = 0.2
    services.settings.compaction.idle_min_tokens = 0
    sess = await services.create_session()
    mark = await services.events.last_seq()
    await turns(services, sess.id, 12)
    await asyncio.sleep(0.6)
    done = [e for e in await since(services, sess.id, mark) if e.type == "compaction.completed"]
    assert len(done) == 1 and done[0].data["trigger"] == "idle"
    # input before the timer fires cancels it
    services.settings.compaction.idle_compact_after_seconds = 0.4
    mark = await services.events.last_seq()
    await turns(services, sess.id, 12, start=50)
    await asyncio.sleep(0.1)
    await turns(services, sess.id, 1, start=90)
    await asyncio.sleep(0.1)
    assert not [e for e in await since(services, sess.id, mark) if e.type == "compaction.started"]


async def test_idle_trigger_off_by_default_and_respects_min_tokens(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    assert not services.coordinator.idle._tasks
    services.settings.compaction.idle_compact_after_seconds = 0.05
    await turns(services, sess.id, 1, start=20)  # default idle_min_tokens=20k: too small
    await asyncio.sleep(0.2)
    assert not aux_calls(gateway)


# ------------------------------------------------------ S4(d) partial manual
@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_partial_compaction_up_to_and_from(client: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    rt = await services.coordinator.runtime(sess.id)
    before = list(rt.history)
    r = await client.post(f"/api/sessions/{sess.id}/compact", json={"up_to": 11})
    assert r.status_code == 200, r.text
    assert r.json()["archived"] == 10 and r.json()["source"] == "summary"
    assert rt.history[0] == before[0] and rt.history[2:] == before[11:]  # everything after kept
    n = len(rt.history)
    keep = list(rt.history[:6])
    r = await client.post(f"/api/sessions/{sess.id}/compact", json={"from": 6})
    assert r.status_code == 200, r.text
    assert rt.history[:6] == keep and len(rt.history) == 7 and r.json()["archived"] == n - 6
    assert rt.history[6]["content"].startswith("<compaction-summary")
    assert (await client.post(f"/api/sessions/{sess.id}/compact", json={"up_to": -1})).status_code == 422


async def test_slash_compact_passes_bounds(services: Services) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    rt = await services.coordinator.runtime(sess.id)
    before = list(rt.history)
    await services.coordinator.submit(sess.id, "/compact up_to=9", interactive=False)
    assert rt.history[2:] == before[9:]


# -------------------------------------------------------------- memory nudge
async def test_memory_nudge_every_n_user_turns(services: Services, gateway: FakeGateway) -> None:
    services.settings.compaction.memory_nudge_interval = 3
    sess = await services.create_session()
    await turns(services, sess.id, 6)
    await services.coordinator.submit(sess.id, "ok", interactive=False)  # trivial: not counted
    await services.coordinator.wait(sess.id, 10)
    main = [r for r in gateway.requests if not r.metadata.get("purpose")]
    flags = [NUDGE in str(r.messages[-1]["content"]) for r in main]
    assert flags == [False, False, True, False, False, True, False]
    assert all(NUDGE not in r.messages[0]["content"] for r in main)  # never in the system prompt
    services.settings.compaction.memory_nudge_interval = 0
    await turns(services, sess.id, 6, start=10)
    assert not any(NUDGE in str(r.messages[-1]["content"]) for r in gateway.requests[-6:])


# --------------------------------------------------------- recall rewrite
async def test_rewrite_memory_query(services: Services, gateway: FakeGateway) -> None:
    from auoreo.memory.rewrite import MAX_CHARS, accept, rewrite_query

    history = [{"role": "user", "content": "we use postgres 16"}, {"role": "assistant", "content": "noted"}]
    out = await rewrite_query(services, "which version do we run?", history)
    assert out == "standalone: which version do we run?"
    req = aux_calls(gateway, "memory_query")[-1]
    assert req.temperature == 0 and req.max_tokens == 96 and req.tool_choice == "none"
    assert "postgres 16" in req.messages[1]["content"]
    assert accept("x" * (MAX_CHARS + 1), "raw") == "raw" and accept("a\nb", "raw") == "raw"
    assert accept('  "Which DB?" ', "raw") == "Which DB?"
    assert await rewrite_query(services, "ok", history) == "ok"  # trivial: no call
    gateway.script.clear()
    real = gateway.stream

    async def boom(request: Any) -> AsyncIterator[Any]:
        if request.metadata.get("purpose") == "memory_query":
            raise GatewayError("SERVER", "down")
        async for item in real(request):
            yield item

    gateway.stream = boom  # type: ignore[method-assign]
    assert await rewrite_query(services, "which version do we run?", history) == "which version do we run?"


async def test_runner_uses_rewritten_query_only_when_enabled(
    services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    class Mem:
        async def context_block(self, query: str, *, allow_aux: bool = True, limit: int = 8) -> str:
            seen.append(query)
            return ""

        async def record_message(self, *a: Any, **k: Any) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(services, "memory", Mem())
    sess = await services.create_session()
    await turns(services, sess.id, 1)
    services.settings.compaction.rewrite_memory_query = True
    await turns(services, sess.id, 1, start=1)
    assert seen[0].startswith("turn 0") and seen[1].startswith("standalone: turn 1")
