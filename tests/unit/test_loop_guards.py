"""G2a (spec §8.1/§8.5/§8.8/§8.9/§8.10): liveness watchdog, stop gates, doom/denial loops,
inbox cancel/claim, sequence repair, `[OUT-OF-BAND]`, LoopSettings warn+fallback."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import LoopSettings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.inbox import Inbox, TurnRequest
from auoreo.runtime.liveness import LivenessWatchdog
from auoreo.runtime.sequence import MISSING_RESULT, PLACEHOLDER_TOOL, repair
from auoreo.schema.events import ServerEvent

from .test_loop_recovery import final, run, turns


async def wait_pending(svc: Services, sid: str) -> list:
    for _ in range(200):
        if pend := svc.approvals.pending(sid):
            return pend
        await asyncio.sleep(0.02)
    raise AssertionError("no approval request")


def drain(sub: object) -> list[ServerEvent]:
    q = sub.queue  # type: ignore[attr-defined]
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ------------------------------------------------------------------ liveness
async def test_liveness_cancels_a_stalled_turn(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.turn_liveness_s = 0.2
    services.settings.loop.turn_liveness_poll_s = 0.05
    gateway.queue(Scripted(text="too late", delay_s=30))
    status, events, sid = await run(services, "hang")
    assert status == "aborted"
    done = final(events)
    assert done.type == "run.aborted" and done.data["error"]["code"] == "LIVENESS"
    assert any(e.type == "turn.liveness_timeout" for e in events)
    assert [e.type for e in events].count("run.aborted") == 1
    # the session is usable afterwards
    gateway.queue(Scripted(text="back"))
    rid = await services.coordinator.submit(sid, "again", interactive=False)
    await services.coordinator.wait(sid, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed"


async def test_stale_watchdog_does_nothing(services: Services) -> None:
    services.settings.loop.turn_liveness_s = 0.05
    services.settings.loop.turn_liveness_poll_s = 0.01
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    dog = LivenessWatchdog(services, rt, "run_x")
    work = asyncio.create_task(asyncio.sleep(0.5))
    dog.start(work)
    rt.generation += 1  # a newer turn owns the runtime
    await asyncio.sleep(0.2)
    assert not work.cancelled() and not dog.fired
    await dog.stop()
    work.cancel()


async def test_pending_approval_counts_as_progress(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.turn_liveness_s = 0.15
    services.settings.loop.turn_liveness_poll_s = 0.03
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo hi"})]), Scripted(text="ran"))
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "x", interactive=True)
    pend = await wait_pending(services, sess.id)
    await asyncio.sleep(0.4)  # a human deciding is not a stall
    await services.approvals.reply(pend[0].request_id, "once")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed"


# ------------------------------------------------------------------ stop gates
async def test_verify_on_stop_nudges_once(services: Services, gateway: FakeGateway) -> None:
    steps = [{"step": "write it", "status": "in_progress", "done_when": "tests pass"}]
    gateway.queue(
        Scripted(tool_calls=[("update_plan", {"steps": steps})]),
        Scripted(text="done (interim)"),
        Scripted(text="still not checked, but final"),
    )
    status, events, sid = await run(services, "plan it")
    assert status == "completed"
    assert final(events).data["text"] == "still not checked, but final"
    hist = await services.coordinator.runner.load_history(sid)
    nudges = [m for m in hist if "<stop-gate:verify_on_stop>" in str(m.get("content"))]
    assert len(nudges) == 1 and "tests pass" in nudges[0]["content"]


async def test_stop_gates_can_be_disabled(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.stop_gates = []
    steps = [{"step": "s", "status": "pending", "done_when": "check"}]
    gateway.queue(Scripted(tool_calls=[("update_plan", {"steps": steps})]), Scripted(text="done"))
    status, _, _ = await run(services, "plan it")
    assert status == "completed" and len(turns(gateway)) == 2


async def test_kanban_gate_nudges_for_claimed_tasks(services: Services, gateway: FakeGateway) -> None:
    kanban = services.ext["teams"].kanban
    sess = await services.create_session()
    task = await kanban.create("default", "ship the thing")
    assert await kanban.claim("default", "me", session_id=sess.id) is not None
    gateway.queue(Scripted(text="all done"), Scripted(text="ok, really done"))
    rid = await services.coordinator.submit(sess.id, "go", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.text == "ok, really done"
    hist = await services.coordinator.runner.load_history(sess.id)
    nudge = [m for m in hist if "<stop-gate:kanban>" in str(m.get("content"))]
    assert len(nudge) == 1 and f"#{task.id}" in nudge[0]["content"]


# ------------------------------------------------------------------ doom / denial loops
async def test_doom_loop_asks_in_interactive_session(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(
        *[Scripted(tool_calls=[("read", {"path": "a.txt"})]) for _ in range(3)], Scripted(text="ok")
    )
    sess = await services.create_session()
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    rid = await services.coordinator.submit(sess.id, "loop", interactive=True)
    pend = await wait_pending(services, sess.id)
    assert pend[0].permission == "doom_loop" and pend[0].pattern == "read" and pend[0].tool == "read"
    await services.approvals.reply(pend[0].request_id, "once")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed" and result.text == "ok"
    assert any(e.type == "run.doom_loop" for e in drain(sub))


async def test_doom_loop_always_stops_checking_the_tool(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(
        *[Scripted(tool_calls=[("read", {"path": "a.txt"})]) for _ in range(6)], Scripted(text="ok")
    )
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "loop", interactive=True)
    pend = await wait_pending(services, sess.id)
    await services.approvals.reply(pend[0].request_id, "always")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed"
    assert (await services.coordinator.runtime(sess.id)).doom_exempt == {"read"}


async def test_doom_loop_reject_blocks(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(*[Scripted(tool_calls=[("read", {"path": "a.txt"})]) for _ in range(3)])
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "loop", interactive=True)
    pend = await wait_pending(services, sess.id)
    await services.approvals.reply(pend[0].request_id, "reject")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "blocked"
    assert result.error is not None and result.error["code"] == "DOOM_LOOP"


async def test_three_consecutive_denials_stop_the_turn(services: Services, gateway: FakeGateway) -> None:
    services.settings.permission_mode = "manual"
    gateway.queue(
        *[Scripted(tool_calls=[("bash", {"command": f"rm -rf /tmp/x{i}"})]) for i in range(3)],
        Scripted(text="never"),
    )
    status, events, sid = await run(services, "delete")
    assert status == "blocked" and final(events).data["error"]["code"] == "DENIAL_LOOP"
    hist = await services.coordinator.runner.load_history(sid)
    denied = [m for m in hist if m["role"] == "tool"]
    assert len(denied) == 3 and all("non-interactive" in m["content"] for m in denied)


# ------------------------------------------------------------------ inbox
def test_inbox_cancel_keeps_or_drops_pending_input() -> None:
    box = Inbox()
    box.steer("do X")
    box.inject("mail arrived")
    box.followup(TurnRequest("run_1", "later"))
    box.cancel("user", keep_inbox=True)
    assert not box.next_step
    assert [r.text for r in box.next_turn] == ["do X\n\n[OUT-OF-BAND] mail arrived", "later"]
    box.steer("Y")
    box.cancel("shutdown", keep_inbox=False)
    assert not box.next_step and not box.next_turn


def test_claim_turn_drains_next_step() -> None:
    box = Inbox()
    box.followup(TurnRequest("run_1", "hello"))
    box.inject("context")
    req = box.claim_turn()
    assert req is not None and req.text == "hello\n\n[OUT-OF-BAND] context"
    assert not box.next_step and box.claim_turn() is None


async def test_user_cancel_moves_steer_to_next_turn(services: Services, gateway: FakeGateway) -> None:
    services.settings.permission_mode = "manual"
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "sleep 30"})]))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "slow", interactive=True)
    await wait_pending(services, sess.id)
    await services.coordinator.submit(sess.id, "then do Y", "steer")
    assert await services.coordinator.cancel(sess.id)
    rt = await services.coordinator.runtime(sess.id)
    assert [r.text for r in rt.inbox.next_turn] == ["then do Y"]


# ------------------------------------------------------------------ sequence repair
def test_repair_merges_same_role_and_fills_tool_gaps() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": [{"type": "text", "text": "b"}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {}}, {"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "next"},
        {"role": "tool", "tool_call_id": "orphan", "content": "r?"},
        {"role": "assistant", "content": "x"},
        {"role": "assistant", "content": "y"},
    ]
    out = repair(msgs)
    assert out[1] == {
        "role": "user",
        "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
    }
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "r1"}
    assert out[4] == {"role": "tool", "tool_call_id": "c2", "content": MISSING_RESULT}
    assert out[5]["role"] == "user"
    assert out[6]["role"] == "assistant" and out[6]["tool_calls"][0]["id"] == "orphan"
    assert out[6]["tool_calls"][0]["function"]["name"] == PLACEHOLDER_TOOL
    assert out[7]["role"] == "tool"
    assert out[8] == {"role": "assistant", "content": "x\n\ny"}
    assert len(out) == 9


def test_repair_leaves_a_valid_sequence_alone() -> None:
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
        {"role": "assistant", "content": "done"},
    ]
    assert repair(msgs) == msgs


async def test_inject_is_marked_out_of_band(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "peer note", "inject")
    gateway.queue(Scripted(text="seen"))
    await services.coordinator.submit(sess.id, "hi", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    user = turns(gateway)[0].messages[-1]["content"]
    assert user.endswith("hi\n\n[OUT-OF-BAND] peer note")


# ------------------------------------------------------------------ settings
def test_loop_settings_warn_and_fall_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        cfg = LoopSettings.model_validate(
            {
                "turn_liveness_s": -5,
                "mot_recovery_limit": "many",
                "run_budget_seconds": 0,
                "token_budget": 5000,
                "stop_gates": ["kanban", "nope"],
                "length_boost_cap": 10,
                "completion_threshold": 2,
                "otk_escalate": "maybe",
            }
        )
    assert cfg.turn_liveness_s == 600 and cfg.mot_recovery_limit == 3
    assert cfg.run_budget_seconds is None and cfg.token_budget == 5000
    assert cfg.stop_gates == ["kanban"] and cfg.length_boost_cap == 32_768
    assert cfg.completion_threshold == 0.0 and cfg.otk_escalate is True
    assert sum("settings.loop." in r.getMessage() for r in caplog.records) == 7
    d = LoopSettings()
    assert (d.delegation_max_iterations, d.turn_liveness_s, d.mot_recovery_limit) == (50, 600, 3)
    assert d.stop_gates == ["verify_on_stop", "pre_verify", "kanban"]
