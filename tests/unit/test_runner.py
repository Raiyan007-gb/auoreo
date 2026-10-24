from __future__ import annotations

import asyncio
from pathlib import Path

from auoreo.core.services import Services
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.schema.events import ServerEvent


async def run(
    svc: Services, text: str, interactive: bool = False, **kw: object
) -> tuple[str, list[ServerEvent]]:
    sess = await svc.create_session()
    sub = svc.bus.subscribe(frozenset({f"session:{sess.id}"}))
    rid = await svc.coordinator.submit(sess.id, text, interactive=interactive, **kw)  # type: ignore[arg-type]
    await svc.coordinator.wait(sess.id, 10)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return result.status, events


def types(events: list[ServerEvent]) -> list[str]:
    return [e.type for e in events if e.type not in {"message.delta", "usage", "session.created"}]


async def test_turn_with_parallel_tool_calls_commits_in_model_order(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    (workdir / "b.txt").write_text("B")
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"}), ("read", {"path": "b.txt"})]),
        Scripted(text="both read"),
    )
    status, events = await run(services, "read both")
    assert status == "completed"
    assert types(events) == [
        "run.started",
        "message.created",
        "step.started",
        "message.completed",
        "tool.started",
        "tool.started",
        "tool.completed",
        "tool.completed",
        "step.started",
        "message.completed",
        "run.completed",
    ]
    done = [e.data["call_id"] for e in events if e.type == "tool.completed"]
    assert done == ["call_1", "call_2"]
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_history_is_persisted_and_valid(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="ok"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "go", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert [m["role"] for m in hist] == ["user", "assistant", "tool", "assistant"]
    assert hist[2]["tool_call_id"] == hist[1]["tool_calls"][0]["id"]
    # the next request carries a byte-identical system prompt (cache-safe)
    await services.coordinator.submit(sess.id, "again", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    systems = {r.messages[0]["content"] for r in gateway.requests}
    assert len(systems) == 1


async def test_doom_loop_stops_turn(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(*[Scripted(tool_calls=[("read", {"path": "a.txt"})]) for _ in range(5)])
    status, events = await run(services, "loop")
    assert status == "blocked"
    blocked = next(e for e in events if e.type == "run.blocked")
    assert blocked.data["error"]["code"] == "DOOM_LOOP"


async def test_step_limit_forces_tool_free_final_answer(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    services.settings.loop.max_iterations = 2
    (workdir / "a.txt").write_text("A")
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"})]),
        Scripted(text="summary", tool_calls=[("read", {"path": "a.txt"})]),
    )
    status, _ = await run(services, "go")
    assert status == "max_steps"
    last = gateway.requests[-1]
    assert last.tool_choice == "none" and "step limit" in last.messages[-1]["content"]


async def test_gateway_error_fails_turn_with_code(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(error=GatewayError("AUTH", "bad key")))
    status, events = await run(services, "hi")
    assert status == "failed"
    assert next(e for e in events if e.type == "run.failed").data["error"]["code"] == "AUTH"


async def test_bare_reject_halts_and_feedback_reject_continues(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "make build"})]))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "build", interactive=True)
    for _ in range(100):
        pend = services.approvals.pending(sess.id)
        if pend:
            break
        await asyncio.sleep(0.02)
    await services.approvals.reply(pend[0].request_id, "reject")
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[-1]["role"] == "tool" and "rejected" in hist[-1]["content"]

    gateway.queue(Scripted(tool_calls=[("bash", {"command": "make build"})]), Scripted(text="adjusted"))
    rid = await services.coordinator.submit(sess.id, "again", interactive=True)
    for _ in range(100):
        pend = services.approvals.pending(sess.id)
        if pend:
            break
        await asyncio.sleep(0.02)
    await services.approvals.reply(pend[0].request_id, "reject", "use make test instead")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed" and result.text == "adjusted"


async def test_approval_once_runs_tool(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo approved"})]), Scripted(text="ran"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "x", interactive=True)
    for _ in range(100):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    await services.approvals.reply(services.approvals.pending(sess.id)[0].request_id, "once")
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert "approved" in hist[2]["content"]


async def test_cancel_aborts_and_keeps_history_valid(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "sleep 30"})]))
    services.settings.permission_mode = "manual"
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "slow", interactive=True)
    for _ in range(100):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    assert await services.coordinator.cancel(sess.id)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[-1]["role"] == "tool" and "aborted" in hist[-1]["content"]
    assert not await services.coordinator.cancel(sess.id)  # idempotent


async def test_steer_during_turn_is_applied_at_next_step(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo wait"})]), Scripted(text="done"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "start", interactive=True)
    for _ in range(100):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    assert await services.coordinator.submit(sess.id, "also do X", "steer") is None
    await services.approvals.reply(services.approvals.pending(sess.id)[0].request_id, "once")
    await services.coordinator.wait(sess.id, 10)
    second = gateway.requests[1]
    assert any(m.get("content") == "also do X" for m in second.messages)


async def test_multi_step_workflow_runs_child_sessions(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    root = workdir / ".auoreo"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "two.yaml").write_text(
        "name: two\ndescription: d\nsteps:\n  - {id: a, agent: default}\n"
        "  - {id: b, agent: default, input: 'refine: {{steps.a.output}}'}\n"
    )
    services.harness.reload()
    gateway.queue(Scripted(text="draft"), Scripted(text="final"))
    status, events = await run(services, "task", workflow="two")
    assert status == "completed"
    assert gateway.requests[1].messages[-1]["content"].endswith("refine: draft")
    steps = [e.data["step"] for e in events if e.type == "workflow.step.completed"]
    assert steps == ["a", "b"]
