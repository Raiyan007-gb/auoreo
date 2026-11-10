"""M7 W1b step 1.3 (`m7.token.context_carrier`): one carrier for the per-turn context block.

`splice` (default) is today's layout: the block is added to the turn's user message at request
time only, so the next turn sends that message without it and the cached prefix breaks there.
`persisted` stores the rendered block with the turn's user message (a message-level key, never
inside `content`), so every later request renders that message identically: the hash chain of
request k is a strict prefix of request k+1, within a tool loop and across turns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.api.routers.sessions import get_messages
from auoreo.core.db import MessageRow
from auoreo.core.services import Services
from auoreo.core.sessions_more import create_share, revert_session, shared_view
from auoreo.core.sessions_ops import export_session
from auoreo.gateway import caching
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import LLMRequest
from auoreo.runtime import request_layout as layout
from auoreo.runtime.compaction_plan import render_transcript
from auoreo.runtime.inbox import RunOptions
from auoreo.runtime.request_layout import CONTEXT_KEY, has_context, strip_context, wire
from auoreo.schema.events import ServerEvent
from auoreo.teams.fork import PLACEHOLDER, exact_history, fork_seed
from tests.teams_helpers import RoutedGateway, new_parent
from tests.teams_helpers import turn as team_turn

PNG = "data:image/png;base64,iVBORw0KGgo="


def main_requests(gateway: FakeGateway) -> list[LLMRequest]:
    return [r for r in gateway.requests if "purpose" not in r.metadata]


async def turn(svc: Services, sid: str, text: str, options: RunOptions | None = None) -> list[ServerEvent]:
    sub = svc.bus.subscribe(frozenset({f"session:{sid}"}))
    rid = await svc.coordinator.submit(sid, text, interactive=False, options=options)
    await svc.coordinator.wait(sid, 15)
    result = svc.coordinator.result(rid or "")
    assert result is not None and result.status == "completed", result
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    return events


async def rows(svc: Services, sid: str) -> list[dict[str, Any]]:
    async with svc.db.session() as s:
        q = select(MessageRow).where(MessageRow.session_id == sid).order_by(MessageRow.idx)
        return [dict(r.message) for r in (await s.execute(q)).scalars()]


def assert_prefix_chain(requests: list[LLMRequest]) -> None:
    chains = [caching.prefix_hashes(r.messages) for r in requests]
    for k, (a, b) in enumerate(zip(chains, chains[1:], strict=False)):
        assert len(b) > len(a) and b[: len(a)] == a, (
            f"request {k + 1} does not extend request {k}: "
            f"diverges at message {caching.divergence(a, b)} of {len(a)}"
        )


@pytest.fixture
def persisted(services: Services, gateway: FakeGateway) -> None:
    services.settings.m7.token.context_carrier = "persisted"
    services.settings.m7.token.telemetry = True
    gateway.telemetry = True


# ------------------------------------------------------------------ helpers
def test_wire_and_strip() -> None:
    plain = {"role": "user", "content": "hi"}
    assert not has_context(plain) and wire(plain) is plain and strip_context(plain) is plain
    marked = {**plain, CONTEXT_KEY: "date: today"}
    assert has_context(marked) and strip_context(marked) == plain
    assert wire(marked) == {"role": "user", "content": "<context>\ndate: today\n</context>\n\nhi"}
    assert marked == {**plain, CONTEXT_KEY: "date: today"}  # neither helper mutates
    image = {"type": "image_url", "image_url": {"url": PNG}}
    parts = {"role": "user", "content": [{"type": "text", "text": "look"}, image], CONTEXT_KEY: "c"}
    assert wire(parts) == {
        "role": "user",
        "content": [
            {"type": "text", "text": "<context>\nc\n</context>"},
            {"type": "text", "text": "look"},
            image,
        ],
    }


# ------------------------------------------------------------------ splice (flag off)
async def test_splice_is_unchanged_and_persists_no_context(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turn(services, sess.id, "first")
    await turn(services, sess.id, "second")
    stored = await rows(services, sess.id)
    assert all(CONTEXT_KEY not in m for m in stored)
    assert [m["content"] for m in stored if m["role"] == "user"] == ["first", "second"]
    one, two = main_requests(gateway)
    assert one.messages[1]["content"].startswith("<context>\n")
    assert one.messages[1]["content"].endswith("\n</context>\n\nfirst")
    assert two.messages[1] == {"role": "user", "content": "first"}  # the block is gone: prefix breaks
    assert two.messages[3]["content"].endswith("\n</context>\n\nsecond")
    assert all(
        set(m) <= {"role", "content", "tool_calls", "tool_call_id"} for r in (one, two) for m in r.messages
    )


# ------------------------------------------------------------------ persisted
async def test_persisted_prefix_only_grows_within_and_across_turns(
    persisted: None, services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    (workdir / "b.txt").write_text("B")
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"})]),
        Scripted(text="read a"),
        Scripted(text="A title"),  # the title aux call after the first turn pops the script too
        Scripted(tool_calls=[("read", {"path": "b.txt"})]),
        Scripted(tool_calls=[("read", {"path": "a.txt"})]),
        Scripted(text="read both"),
        Scripted(text="bye"),
    )
    sess = await services.create_session()
    events = await turn(services, sess.id, "one")
    events += await turn(services, sess.id, "two")
    events += await turn(services, sess.id, "three")
    requests = main_requests(gateway)
    assert len(requests) == 6
    assert_prefix_chain(requests)
    assert not [e for e in events if e.type == "cache.prefix_changed"]
    assert len([e for e in events if e.type == "usage" and "cache.prefix_hash" in e.data]) == 6
    # every turn's user message is sent with its own block, in every later request
    last = requests[-1].messages
    users = [m["content"] for m in last if m["role"] == "user"]
    assert len(users) == 3 and all(u.startswith("<context>\n") for u in users)
    assert [u.rsplit("</context>\n\n", 1)[1] for u in users] == ["one", "two", "three"]
    assert all(CONTEXT_KEY not in m for r in requests for m in r.messages)  # never on the wire

    # a cold start (history reloaded from the database) continues the same prefix
    services.coordinator.forget(sess.id)
    events = await turn(services, sess.id, "four")
    assert_prefix_chain(main_requests(gateway))
    assert not [e for e in events if e.type == "cache.prefix_changed"]


async def test_persisted_block_lives_beside_the_content(
    persisted: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turn(services, sess.id, "/context")
    stored = await rows(services, sess.id)
    rt = await services.coordinator.runtime(sess.id)
    assert stored == rt.history  # the row was updated, not only the live history
    user = stored[0]
    assert user["role"] == "user" and user["content"] == "/context" and user[CONTEXT_KEY]
    # the model saw exactly the stored block (the fake echoes the block of `/context`)
    assert stored[1]["content"] == f"context: {user[CONTEXT_KEY].strip()}"
    sent = main_requests(gateway)[0].messages[1]
    assert sent == {"role": "user", "content": f"<context>\n{user[CONTEXT_KEY]}\n</context>\n\n/context"}


async def test_readers_never_show_the_block(
    persisted: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    events = await turn(services, sess.id, "hello there")
    rt = await services.coordinator.runtime(sess.id)
    assert has_context(rt.history[0])
    block = rt.history[0][CONTEXT_KEY]
    shown = await get_messages(sess.id, services)
    assert [m.content for m in shown] == ["hello there", "echo: hello there"]
    for text in (
        fork_seed(rt.history, 10_000),
        render_transcript(rt.history),
        await export_session(services, sess.id, "md"),
        str([e.data for e in events if e.type in {"message.created", "message.completed", "run.completed"}]),
    ):
        assert "hello there" in text and "<context>" not in text and block not in text
    assert CONTEXT_KEY not in await export_session(services, sess.id, "json")
    token = await create_share(services, sess.id)
    view = await shared_view(services, token)
    assert view is not None and view["messages"][0] == {"role": "user", "content": "hello there"}
    services.coordinator.forget(sess.id)
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    assert await revert_session(services, sess.id, -1) == 2
    reverted = sub.queue.get_nowait()
    assert reverted.type == "session.reverted" and reverted.data["removed_messages"][0] == view["messages"][0]


async def test_recall_runs_before_the_block_is_attached(
    persisted: None, services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []

    async def spy(svc: Services, rt: Any, text: str) -> str:
        seen.append(([dict(m) for m in rt.history[-7:-1]], dict(rt.history[-1])))
        return text

    monkeypatch.setattr("auoreo.runtime.nudges.recall_query", spy)  # memory/recall.py imports it per call
    sess = await services.create_session()
    await turn(services, sess.id, "first")
    await turn(services, sess.id, "second")
    (recent1, own1), (recent2, own2) = seen
    assert recent1 == [] and own1 == {"role": "user", "content": "first"}
    # the turn's own message is still last and unmarked when the recall query is built ...
    assert own2 == {"role": "user", "content": "second"}
    # ... and earlier messages keep their plain content (the block is a separate key)
    assert [m["content"] for m in recent2] == ["first", "echo: first"]
    assert "<context>" not in str([m["content"] for m in recent2])


async def test_attachments_keep_their_parts(
    persisted: None, services: Services, gateway: FakeGateway
) -> None:
    image = {"type": "image_url", "image_url": {"url": PNG}}
    sess = await services.create_session()
    await turn(services, sess.id, "look", RunOptions(attachments=(image,)))
    (stored, _answer) = await rows(services, sess.id)
    assert stored["content"] == [{"type": "text", "text": "look"}, image] and stored[CONTEXT_KEY]
    sent = main_requests(gateway)[0].messages[1]["content"]
    assert sent[0]["text"] == f"<context>\n{stored[CONTEXT_KEY]}\n</context>"
    assert sent[1:] == [{"type": "text", "text": "look"}, image]
    await turn(services, sess.id, "and now?")
    assert_prefix_chain(main_requests(gateway))


async def test_switching_back_to_splice_keeps_old_turns_stable(
    persisted: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turn(services, sess.id, "kept")
    services.settings.m7.token.context_carrier = "splice"
    await turn(services, sess.id, "spliced")
    stored = [m for m in await rows(services, sess.id) if m["role"] == "user"]
    assert has_context(stored[0]) and not has_context(stored[1])
    one, two = main_requests(gateway)
    assert two.messages[: len(one.messages)] == one.messages  # the persisted turn still matches
    assert two.messages[-1]["content"].endswith("\n</context>\n\nspliced")


async def test_no_block_is_stored_without_a_context(
    persisted: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    runner = services.coordinator.runner
    msg = {"role": "user", "content": "x"}
    await runner._persist(rt, msg)
    await layout.carry(runner, rt, rt.history[-1], "")  # an exact fork renders no context
    assert rt.history[-1] == {"role": "user", "content": "x"} and await rows(services, sess.id) == [msg]


# ------------------------------------------------------------------ fork and compaction readers
def test_exact_history_does_not_splice_a_persisted_block_twice() -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "delegate_task", "arguments": "{}"}}
    turn_msg = {"role": "user", "content": "go", CONTEXT_KEY: "ctx"}
    history = [turn_msg, {"role": "assistant", "content": None, "tool_calls": [call]}]
    out = exact_history(history, turn_msg, "ctx")
    assert out[0] == turn_msg and out[0] is not turn_msg  # the child renders it the same way
    assert out[2] == {"role": "tool", "tool_call_id": "c1", "content": PLACEHOLDER}
    legacy = {"role": "user", "content": "go"}
    assert exact_history([legacy], legacy, "ctx")[0]["content"] == "<context>\nctx\n</context>\n\ngo"


async def test_summarizer_head_is_sent_in_wire_form(services: Services) -> None:
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    head = [{"role": "user", "content": "start", CONTEXT_KEY: "ctx"}]
    middle = [{"role": "assistant", "content": "work"}]
    summarizer = services.coordinator.runner.compaction.summarizer
    request = summarizer._request(rt, "cmp_1", "default", "default", head, middle)
    assert request.messages[1] == {"role": "user", "content": "<context>\nctx\n</context>\n\nstart"}
    assert all(CONTEXT_KEY not in m for m in request.messages)
    plain = summarizer._request(rt, "cmp_1", "default", "default", [strip_context(head[0])], middle)
    assert plain.messages[1] == {"role": "user", "content": "start"}


@pytest.mark.parametrize("carrier", ["splice", "persisted"])
async def test_exact_fork_shares_the_parent_prefix(home: Path, workdir: Path, carrier: str) -> None:
    from auoreo.core.settings import Settings

    gateway = RoutedGateway()
    settings = Settings(home=home, gateway="fake", approval_timeout_s=5)
    settings.m7.token.context_carrier = carrier  # type: ignore[assignment]
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        args = {"goal": "FORKED-TASK count files", "name": "twin", "mode": "exact"}
        gateway.route(
            "PARENT-EXACT",
            Scripted(tool_calls=[("delegate_task", args)]),
            Scripted(text="fork result"),
            Scripted(text="parent done"),
        )
        parent = await new_parent(svc)
        result = await team_turn(svc, parent, "PARENT-EXACT please fork")
        assert result.status == "completed" and result.text == "parent done"
        first, child = gateway.requests_for("PARENT-EXACT")[:2]
        assert child.metadata["session_id"] != parent
        assert child.messages[: len(first.messages)] == first.messages  # cacheable prefix
        assert first.messages[1]["content"].startswith("<context>\n")
        assert all(CONTEXT_KEY not in m for m in child.messages)
        assert "<fork-directive>" in child.messages[-1]["content"]
        assert not child.messages[-1]["content"].startswith("<context>")  # the fork adds no block
    finally:
        await svc.close()
