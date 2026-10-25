"""G2a (spec §8.6/§8.7/§8.9): OTK/length recovery, empty responses, outer-loop error cap,
IterationBudget with one grace call, wall-clock and token budgets."""

from __future__ import annotations

from pathlib import Path

from auoreo.core.services import Services
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.recovery import OTK_ESCALATED, RESUME_NOTE
from auoreo.schema.events import ServerEvent


async def run(
    svc: Services, text: str, *, parent: str | None = None, interactive: bool = False
) -> tuple[str, list[ServerEvent], str]:
    sess = await svc.create_session(parent_id=parent)
    sub = svc.bus.subscribe(frozenset({f"session:{sess.id}"}))
    rid = await svc.coordinator.submit(sess.id, text, interactive=interactive)
    await svc.coordinator.wait(sess.id, 15)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return result.status, events, sess.id


def turns(gateway: FakeGateway) -> list:
    return [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]


def final(events: list[ServerEvent]) -> ServerEvent:
    return next(e for e in events if e.type.startswith("run.") and e.type.split(".")[1] in _TERMINAL)


_TERMINAL = {"completed", "failed", "blocked", "aborted", "max_steps"}


# ------------------------------------------------------------------ OTK / length
async def test_length_cut_recovered_across_two_continuations(
    services: Services, gateway: FakeGateway
) -> None:
    services.settings.loop.otk_escalate = False
    gateway.queue(
        Scripted(text="Hello, ", finish="length"),
        Scripted(text="wor", finish="length"),
        Scripted(text="ld."),
    )
    status, events, sid = await run(services, "greet")
    assert status == "completed"
    assert final(events).data["text"] == "Hello, world."
    assert "truncated" not in final(events).data
    rec = next(e for e in events if e.type == "run.length_recovered").data
    assert rec["continuations"] == 2 and rec["escalated"] is False
    reqs = turns(gateway)
    assert len(reqs) == 3
    # meta resume nudges: request-only, partial text as the assistant turn before them
    assert reqs[1].messages[-1]["content"] == RESUME_NOTE
    assert reqs[1].messages[-2] == {"role": "assistant", "content": "Hello, "}
    assert reqs[2].messages[-2] == {"role": "assistant", "content": "Hello, wor"}
    assert [r.max_tokens for r in reqs] == [None, 16_384, 32_768]  # 2x/4x boost, cap 32768
    hist = await services.coordinator.runner.load_history(sid)
    assert [m["role"] for m in hist] == ["user", "assistant"]  # one assistant message
    assert hist[1]["content"] == "Hello, world."


async def test_length_cut_escalates_max_tokens_once(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text="draft that got cut", finish="length"), Scripted(text="full answer"))
    status, events, _ = await run(services, "write")
    assert status == "completed" and final(events).data["text"] == "full answer"
    assert any(e.type == "message.reset" for e in events)
    rec = next(e for e in events if e.type == "run.length_recovered").data
    assert rec == {"run_id": rec["run_id"], "continuations": 0, "escalated": True}
    reqs = turns(gateway)
    assert reqs[1].max_tokens == OTK_ESCALATED and reqs[1].messages == reqs[0].messages


async def test_length_cut_gives_up_completed_truncated(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.otk_escalate = False
    services.settings.loop.mot_recovery_limit = 1
    gateway.queue(Scripted(text="a", finish="length"), Scripted(text="b", finish="length"))
    status, events, _ = await run(services, "long")
    assert status == "completed"
    done = final(events)
    assert done.type == "run.completed" and done.data["truncated"] is True and done.data["text"] == "ab"
    assert any(e.type == "run.length_truncated" for e in events)


# ------------------------------------------------------------------ empty responses
async def test_empty_response_retried_once_with_nudge(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text=""), Scripted(text="there you go"))
    status, events, _ = await run(services, "hi")
    assert status == "completed" and final(events).data["text"] == "there you go"
    assert any(e.type == "run.empty_response" for e in events)
    assert "empty" in turns(gateway)[1].messages[-1]["content"]


async def test_empty_response_twice_fails(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text=""), Scripted(text=""))
    status, events, _ = await run(services, "hi")
    assert status == "failed" and final(events).data["error"]["code"] == "EMPTY_RESPONSE"


# ------------------------------------------------------------------ outer-loop errors
async def test_retryable_errors_retry_then_succeed(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(
        Scripted(error=GatewayError("SERVER", "boom")),
        Scripted(error=GatewayError("TIMEOUT", "slow")),
        Scripted(text="ok"),
    )
    status, events, _ = await run(services, "hi")
    assert status == "completed"
    assert [e.data["errors"] for e in events if e.type == "run.retry"] == [1, 2]


async def test_outer_loop_error_cap(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.outer_loop_errors = 2
    gateway.queue(*[Scripted(error=GatewayError("SERVER", "boom")) for _ in range(3)], Scripted(text="x"))
    status, events, _ = await run(services, "hi")
    assert status == "failed" and final(events).data["error"]["code"] == "SERVER"
    assert len(turns(gateway)) == 2


# ------------------------------------------------------------------ budgets
async def test_subagent_budget_exhaustion_makes_exactly_one_grace_call(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    for n in "abc":
        (workdir / f"{n}.txt").write_text(n)
    services.settings.loop.delegation_max_iterations = 2
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"})]),
        Scripted(tool_calls=[("read", {"path": "b.txt"})]),
        Scripted(text="wrapping up", tool_calls=[("read", {"path": "c.txt"})]),  # grace: tools dropped
        Scripted(text="never sent"),
    )
    parent = await services.create_session()
    status, events, sid = await run(services, "work", parent=parent.id)
    assert status == "blocked"
    err = final(events).data["error"]
    assert err["code"] == "BUDGET_EXHAUSTED" and "iterations: 2/2" in err["message"]
    reqs = turns(gateway)
    assert len(reqs) == 3  # two budgeted calls + exactly one grace call
    assert reqs[-1].tool_choice == "none" and "budget" in reqs[-1].messages[-1]["content"]
    assert [r.tool_choice for r in reqs[:2]] == ["auto", "auto"]
    assert final(events).data["text"] == "wrapping up"
    assert len(gateway.script) == 1
    hist = await services.coordinator.runner.load_history(sid)
    assert "tool_calls" not in hist[-1]


async def test_free_tool_failures_are_refunded(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    services.settings.loop.delegation_max_iterations = 2
    gateway.queue(
        Scripted(tool_calls=[("no_such_tool", {})]),
        Scripted(tool_calls=[("read", {"bogus": 1})]),
        Scripted(tool_calls=[("read", {"path": "a.txt"})]),
        Scripted(text="done"),
    )
    parent = await services.create_session()
    status, events, _ = await run(services, "work", parent=parent.id)
    assert status == "completed"
    summary = next(e for e in events if e.type == "turn.summary").data
    assert summary["budget"] == {"used": 2, "total": 2} and summary["steps"] == 4


async def test_wall_clock_budget_wraps_up_then_grace(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    (workdir / "b.txt").write_text("B")
    services.settings.loop.run_budget_seconds = 1.0
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"})], delay_s=0.85),  # past 80 %
        Scripted(tool_calls=[("read", {"path": "b.txt"})], delay_s=0.3),  # past 100 %
        Scripted(text="final summary"),
    )
    status, events, sid = await run(services, "slow work")
    assert status == "blocked"
    assert "wall_clock" in final(events).data["error"]["message"]
    hist = await services.coordinator.runner.load_history(sid)
    wraps = [m for m in hist if m["role"] == "user" and "Wrap up" in str(m["content"])]
    assert len(wraps) == 1 and wraps[0]["content"].startswith("[OUT-OF-BAND]")  # latched: once
    assert turns(gateway)[-1].tool_choice == "none"


async def test_token_budget_exhaustion(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    services.settings.loop.token_budget = 10
    gateway.queue(Scripted(text="x" * 100, tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="sum"))
    status, events, _ = await run(services, "go")
    assert status == "blocked" and "tokens" in final(events).data["error"]["message"]
    assert len(turns(gateway)) == 2


async def test_token_budget_keep_going_with_diminishing_returns(
    services: Services, gateway: FakeGateway
) -> None:
    services.settings.loop.token_budget = 100_000
    services.settings.loop.completion_threshold = 0.9
    gateway.queue(*[Scripted(text=f"bit {i}") for i in range(6)])
    status, _, sid = await run(services, "go")
    assert status == "completed"
    hist = await services.coordinator.runner.load_history(sid)
    nudges = [m for m in hist if m["role"] == "user" and "Keep working" in str(m["content"])]
    assert len(nudges) == 3  # 3 small continuations, then diminishing returns stops it
