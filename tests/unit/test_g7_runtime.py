"""G7: reasoning stream (§17 thinking toggle), ask_user_question shapes (§25.7), approvals
one-shot/cascade/audit link (§17), usage time series."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from auoreo.core.approvals import ApprovalBroker, pending_dict, reply_with_grants
from auoreo.core.questions import InvalidAnswer, QuestionBroker, normalize
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import LLMRequest, ReasoningDelta, TextDelta
from auoreo.gateway.usage_parse import reasoning_of


# ------------------------------------------------------------------ reasoning
def test_reasoning_of_maps_litellm_deltas() -> None:
    assert reasoning_of(None) == []
    assert reasoning_of(SimpleNamespace(reasoning_content="hmm", content=None)) == ["hmm"]
    blocks = [{"type": "thinking", "thinking": "a"}, SimpleNamespace(thinking="b"), {"type": "redacted"}]
    assert reasoning_of(SimpleNamespace(reasoning_content=None, thinking_blocks=blocks)) == ["a", "b"]
    assert reasoning_of(SimpleNamespace(content="text")) == []


async def test_fake_gateway_streams_reasoning_on_request() -> None:
    gw = FakeGateway([Scripted(text="answer", reasoning="first think")])
    req = LLMRequest(model="m", messages=({"role": "user", "content": "q"},))
    items = [i async for i in gw.stream(req)]
    assert "".join(i.text for i in items if isinstance(i, ReasoningDelta)) == "first think"
    assert "".join(i.text for i in items if isinstance(i, TextDelta)) == "answer"
    req2 = LLMRequest(model="m", messages=({"role": "user", "content": "/think the sky"},))
    items2 = [i async for i in gw.stream(req2)]
    assert any(isinstance(i, ReasoningDelta) and "the sky" in i.text for i in items2)
    assert "".join(i.text for i in items2 if isinstance(i, TextDelta)) == "echo: the sky"


async def test_runner_emits_message_reasoning_events(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text="final", reasoning="step one, then step two"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "think", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    thought = "".join(e.data["text"] for e in events if e.type == "message.reasoning")
    assert thought == "step one, then step two"
    first_reason = next(e.seq for e in events if e.type == "message.reasoning")
    first_text = next(e.seq for e in events if e.type == "message.delta")
    assert first_reason < first_text
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[-1]["content"] == "final"  # thinking never enters the answer


# ------------------------------------------------------------------ questions
QS: list[dict[str, Any]] = [
    {"question": "Which db?", "options": [{"label": "sqlite"}, {"label": "postgres"}]},
    {"question": "Strict?", "options": [{"label": "yes"}, {"label": "no"}], "allowCustom": False},
    {"question": "Name?"},
]


def test_normalize_custom_text_and_validation() -> None:
    out = normalize(
        QS,
        [
            {"answers": ["sqlite"], "customText": "with WAL"},
            {"answers": ["no"]},
            {"answer": "orders"},  # legacy flat form
        ],
    )
    assert out[0]["answers"] == ["sqlite"] and out[0]["customText"] == "with WAL"
    assert out[1] == {"question": "Strict?", "answers": ["no"], "answer": "no"}
    assert out[2]["answers"] == ["orders"] and out[2]["customText"] == "orders"
    with pytest.raises(InvalidAnswer, match="only accepts its options"):
        normalize(QS, [{}, {"answers": ["maybe"]}, {}])
    with pytest.raises(InvalidAnswer, match="single-select"):
        normalize(QS, [{"answers": ["sqlite", "postgres"]}])
    multi = [{"question": "Pick", "options": [{"label": "a"}, {"label": "b"}], "multiSelect": True}]
    assert normalize(multi, [{"answers": ["a", "b"]}])[0]["answers"] == ["a", "b"]


async def test_question_timeout_per_call_and_events() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def emit(sid: str, t: str, d: dict[str, Any]) -> None:
        seen.append((t, d))

    broker = QuestionBroker(emit, timeout_s=300)
    out = await broker.ask("s", [{"question": "?"}], timeout_s=0.05)
    assert out == {"responses": [], "timed_out": True}
    asked = seen[0][1]
    assert asked["timeout_s"] == 0.05 and asked["expires_at"] > 0
    assert seen[-1] == ("question.answered", {"request_id": asked["request_id"], "timed_out": True})


async def test_invalid_answer_keeps_question_open() -> None:
    async def emit(sid: str, t: str, d: dict[str, Any]) -> None:
        return None

    broker = QuestionBroker(emit)
    task = asyncio.create_task(broker.ask("s", QS[1:2], timeout_s=5))
    await asyncio.sleep(0.01)
    rid = broker.pending()[0].request_id
    with pytest.raises(InvalidAnswer):
        await broker.answer(rid, [{"answers": ["free text"]}])
    assert broker.pending() and await broker.answer(rid, [{"answers": ["yes"]}])
    assert (await task)["responses"][0]["answers"] == ["yes"]


async def test_ask_user_question_defaults_and_custom_text(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(
        Scripted(tool_calls=[("ask_user_question", {"questions": [QS[0]]})]),
        Scripted(text="ok"),
    )
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "db?", interactive=True)
    for _ in range(200):
        if services.questions.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    q = services.questions.pending(sess.id)[0]
    assert q.timeout_s == 60  # spec §25.7 default
    assert q.questions[0]["allowCustom"] is True and q.questions[0]["multiSelect"] is False
    await services.questions.answer(q.request_id, [{"answers": ["postgres"], "customText": "v17"}])
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    result = json.loads(next(m for m in hist if m["role"] == "tool")["content"])
    assert result == {
        "responses": [{"question": "Which db?", "answers": ["postgres"], "customText": "v17"}],
        "timed_out": False,
    }


# ------------------------------------------------------------------ approvals
async def _broker() -> tuple[ApprovalBroker, list[tuple[str, str, dict[str, Any]]]]:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    async def emit(sid: str, t: str, d: dict[str, Any]) -> None:
        seen.append((sid, t, d))

    return ApprovalBroker(emit, timeout_s=5), seen


async def test_one_shot_requests_never_persist_always() -> None:
    broker, seen = await _broker()
    granted: list[str] = []
    engine = SimpleNamespace(grant_always=lambda perm, pat: granted.append(pat), evaluate=None)
    task = asyncio.create_task(
        broker.request("s1", "bash", "sandbox_escalate", "workspace-write:x", "d", one_shot=True)
    )
    await asyncio.sleep(0.01)
    p = broker.pending()[0]
    assert pending_dict(p)["one_shot"] is True and seen[0][2]["one_shot"] is True
    assert await reply_with_grants(broker, lambda sid: engine, p.request_id, "always")  # type: ignore[arg-type,return-value]
    outcome = await task
    assert outcome.granted and outcome.reply == "once" and outcome.request_id == p.request_id
    assert granted == []


async def test_cascade_approves_the_matching_group_only() -> None:
    broker, seen = await _broker()
    same = [
        asyncio.create_task(broker.request(sid, "bash", "bash", f"echo {i}", "d", cwd="/w"))
        for i, sid in enumerate(["a", "b", "c"])
    ]
    other = asyncio.create_task(broker.request("a", "write", "edit", "x.py", "d", cwd="/w"))
    await asyncio.sleep(0.01)
    first = next(p for p in broker.pending() if p.session_id == "a" and p.tool == "bash")
    assert await reply_with_grants(broker, lambda sid: None, first.request_id, "once", cascade=True)
    results = await asyncio.gather(*same)
    assert all(r.granted and r.reply == "once" for r in results)
    assert [p.tool for p in broker.pending()] == ["write"]
    cascaded = [d for _, t, d in seen if t == "permission.replied" and d.get("cascade")]
    assert len(cascaded) == 2 and all(d["via"] == first.request_id for d in cascaded)
    other.cancel()


async def test_cascade_never_crosses_one_shot_requests() -> None:
    """A `once` + cascade on the `dev` ssh card must not approve a `prod` ssh request from
    another session: one-shot requests (ssh, net_direct, sandbox escalation) are decided one
    at a time."""
    broker, seen = await _broker()
    dev = asyncio.create_task(
        broker.request(
            "sessA", "exec_command", "ssh", "dev", "ssh u@dev:22 (dev)", one_shot=True, justification="logs"
        )
    )
    prod = asyncio.create_task(
        broker.request(
            "sessB",
            "exec_command",
            "ssh",
            "prod",
            "ssh root@prod:22 (prod)",
            one_shot=True,
            justification="x",
        )
    )
    await asyncio.sleep(0.01)
    first = next(p for p in broker.pending() if p.pattern == "dev")
    assert await reply_with_grants(broker, lambda sid: None, first.request_id, "once", cascade=True)
    assert (await dev).granted
    assert [p.pattern for p in broker.pending()] == ["prod"]
    assert not [d for _, t, d in seen if t == "permission.replied" and d.get("cascade")]
    assert await broker.reply(broker.pending()[0].request_id, "reject")
    assert not (await prod).granted


async def test_executor_audit_row_links_the_request_id(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "echo hi"})]), Scripted(text="done"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "run", interactive=True)
    for _ in range(200):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    p = services.approvals.pending(sess.id)[0]
    await services.approvals.reply(p.request_id, "once")
    await services.coordinator.wait(sess.id, 10)
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    q = select(AuditRow).where(AuditRow.session_id == sess.id, AuditRow.action == "permission.reply")
    async with services.db.session() as s:
        rows = list((await s.execute(q)).scalars())
    assert any(services.audit.plain_detail(r).get("request_id") == p.request_id for r in rows)


# ------------------------------------------------------------------ usage
async def test_usage_series_buckets(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "hello", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    ledger = services.coordinator.runner.usage
    days = await ledger.series(7, "day")
    assert len(days) >= 7 and sum(p["calls"] for p in days) >= 1
    hours = await ledger.series(1, "hour")
    assert len(hours) >= 24 and sum(p["prompt_tokens"] for p in hours) > 0
