"""M7 W1b step 1.1 (`m7.token.telemetry`): prefix/tool hashes and breakpoints on the `usage`
event and the `llm.call` span, the `cache.prefix_changed` warning, tool counts on `step.started`.

`test_splice_layout_diverges_at_the_previous_turns_user_message` pins today's defect: the per-turn
context block is spliced at request time and never persisted, so the next turn's prefix differs at
the previous turn's user message (the fix is `m7.token.context_carrier = persisted`, see
`test_request_layout.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from auoreo.core import observability as obs
from auoreo.core.event_levels import level_of
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway import caching
from auoreo.gateway.caching import CacheInfo
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.pipeline import ShapeContext, shape
from auoreo.runtime.request_layout import PrefixTracker
from auoreo.schema.events import ServerEvent

KEYS = {"cache.prefix_hash", "tools.hash", "cache.breakpoints"}
SYSTEM = {"role": "system", "content": "sys"}


async def turn(svc: Services, sid: str, text: str) -> list[ServerEvent]:
    sub = svc.bus.subscribe(frozenset({f"session:{sid}"}))
    rid = await svc.coordinator.submit(sid, text, interactive=False)
    await svc.coordinator.wait(sid, 15)
    result = svc.coordinator.result(rid or "")
    assert result is not None and result.status == "completed", result
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    return events


def of(events: list[ServerEvent], type_: str) -> list[dict[str, Any]]:
    return [e.data for e in events if e.type == type_]


# ------------------------------------------------------------------ hashing
def test_prefix_hashes_form_a_chain() -> None:
    a = [SYSTEM, {"role": "user", "content": "hi"}]
    b = [*a, {"role": "assistant", "content": "yo"}]
    ha, hb = caching.prefix_hashes(a), caching.prefix_hashes(b)
    assert hb[: len(ha)] == ha and len(hb) == 3 and len(set(hb)) == 3
    assert all(len(h) == caching.HASH_CHARS for h in hb)
    assert caching.divergence(ha, hb) == 2  # the earlier request is a full prefix
    changed = caching.prefix_hashes([SYSTEM, {"role": "user", "content": "hi!"}, b[2]])
    assert caching.divergence(hb, changed) == 1
    assert changed[2] != hb[2]  # a change poisons every later entry
    assert caching.divergence(hb, ha) == 2  # a shorter request diverges at its end


def test_hashes_ignore_cache_marks_but_not_content() -> None:
    plain = [SYSTEM, {"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": "c", "content": "r"}]
    kwargs = {
        "model": "anthropic/claude-x",
        "messages": plain,
        "tools": [{"type": "function", "function": {}}],
    }
    shaped = shape(kwargs, ShapeContext(model="anthropic/claude-x", cache_layout="tail"))
    assert "cache_control" in str(shaped["messages"]) and "cache_control" in str(shaped["tools"])
    assert caching.prefix_hashes(shaped["messages"]) == caching.prefix_hashes(plain)
    assert caching.tools_hash(shaped["tools"]) == caching.tools_hash(kwargs["tools"])
    assert caching.breakpoints(shaped) == ("tools", 0, 1, 2)
    assert caching.breakpoints(kwargs) == ()
    assert caching.tools_hash([]) != caching.tools_hash(kwargs["tools"])


def test_images_are_hashed_by_reference() -> None:
    def msg(data: str) -> dict[str, Any]:
        url = f"data:image/png;base64,{data}"
        return {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}

    big = "A" * 200_000
    assert big not in caching.canonical(msg(big)) and len(caching.canonical(msg(big))) < 300
    assert caching.prefix_hashes([msg(big)]) != caching.prefix_hashes([msg("B" * 200_000)])
    assert caching.prefix_hashes([msg(big)]) == caching.prefix_hashes([msg(big)])


def test_describe_carries_no_message_text() -> None:
    info = caching.describe({"messages": [SYSTEM, {"role": "user", "content": "SECRET-TEXT"}]})
    assert info.roles == ("system", "user") and info.prefix_hash == info.prefix_hashes[-1]
    assert set(info.event_data()) == KEYS and set(info.span_attrs()) == KEYS
    assert "SECRET-TEXT" not in repr(info)
    assert caching.describe({"messages": []}).prefix_hash == ""


# ------------------------------------------------------------------ tracker
def info_of(*contents: str, tools: str = "t") -> CacheInfo:
    msgs = [{"role": "user", "content": c} for c in contents]
    return CacheInfo(caching.prefix_hashes(msgs), tools, (), tuple("user" for _ in msgs))


def test_tracker_reports_only_a_broken_prefix() -> None:
    t = PrefixTracker()
    assert t.observe("s", "m", info_of("a", "b")) is None  # first request: nothing to compare
    assert t.observe("s", "m", info_of("a", "b", "c")) is None  # extends
    change = t.observe("s", "m", info_of("a", "X", "c", "d"))
    assert change == {"index": 1, "expected_index": 2, "previous_messages": 3, "messages": 4, "role": "user"}
    assert t.observe("s", "m", info_of("a", "X", "c", "d", "e")) is None  # the new baseline
    assert t.observe("s", "m", info_of("a", "X")) is not None  # a shorter request lost its tail


def test_tracker_resets_instead_of_warning() -> None:
    t = PrefixTracker()
    t.observe("s", "m", info_of("a", "b"))
    assert t.observe("s", "other-deployment", info_of("z")) is None  # model switch / fallback
    assert t.observe("s", "other-deployment", info_of("y"), reset=True) is None  # compaction
    assert t.observe("s", "other-deployment", info_of("x", tools="t2")) is None  # tools changed
    assert t.observe("s", "other-deployment", info_of("w", tools="t2")) is not None
    assert t.observe("s2", "m", info_of("q")) is None  # sessions are independent
    t.forget("s")
    assert t.observe("s", "other-deployment", info_of("v", tools="t2")) is None


def test_tracker_ignores_a_request_only_tail_and_is_bounded() -> None:
    t = PrefixTracker(capacity=2)
    t.observe("s", "m", info_of("a", "note"), volatile_tail=True)
    assert t.observe("s", "m", info_of("a", "b")) is None  # the note was never part of the prefix
    for sid in ("s1", "s2", "s3"):
        t.observe(sid, "m", info_of("a"))
    assert len(t) == 2
    assert t.observe("s", "m", info_of("zzz")) is None  # evicted: no baseline left


def test_event_severity() -> None:
    assert level_of("cache.prefix_changed") == "warn"


# ------------------------------------------------------------------ end to end (fake gateway)
@pytest.fixture
def telemetry(services: Services, gateway: FakeGateway) -> None:
    services.settings.m7.token.telemetry = True
    gateway.telemetry = True


async def test_flag_off_changes_nothing(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="done"))
    sess = await services.create_session()
    events = await turn(services, sess.id, "read a") + await turn(services, sess.id, "again")
    usage = of(events, "usage")
    assert len(usage) == 3 and all(not KEYS & set(u) for u in usage)
    assert all(set(s) == {"run_id", "step", "tools"} for s in of(events, "step.started"))
    assert not of(events, "cache.prefix_changed")


async def test_splice_layout_diverges_at_the_previous_turns_user_message(
    telemetry: None, services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="done"))
    sess = await services.create_session()
    first = await turn(services, sess.id, "read a")
    usage = of(first, "usage")
    assert len(usage) == 2 and all(set(u) >= KEYS for u in usage)
    assert usage[0]["cache.prefix_hash"] != usage[1]["cache.prefix_hash"]
    assert usage[0]["tools.hash"] == usage[1]["tools.hash"]
    assert usage[0]["cache.breakpoints"] == []  # the fake model is not in the Anthropic family
    assert not of(first, "cache.prefix_changed")  # inside a tool loop the prefix only grows

    second = await turn(services, sess.id, "and again")
    (changed,) = of(second, "cache.prefix_changed")
    rt = await services.coordinator.runtime(sess.id)
    turn1_user = next(i for i, m in enumerate(rt.history) if m["role"] == "user") + 1  # + system
    assert changed["index"] == turn1_user == 1 and changed["role"] == "user"
    assert changed["expected_index"] == 3 and changed["previous_messages"] == 4
    assert changed["run_id"] and changed["model"]
    assert "read a" not in str(changed) and "<context>" not in str(changed)


async def test_step_started_counts_deferred_and_loaded_tools(
    telemetry: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    (step,) = of(await turn(services, sess.id, "hello"), "step.started")
    assert step["loaded"] == 0 and step["deferred"] > 0 and step["tools"] > 0
    rt = await services.coordinator.runtime(sess.id)
    spec = services.coordinator.runner._spec(rt, rt.agent, None)
    assert step["deferred"] == len(spec.deferred)
    rt.state.loaded_tools.add(spec.deferred[0].name)
    (step,) = of(await turn(services, sess.id, "again"), "step.started")
    assert step["loaded"] == 1 and step["deferred"] == len(spec.deferred) - 1


async def test_breakpoints_are_reported_for_an_anthropic_family_model(
    telemetry: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session(model="anthropic/claude-test")
    (usage,) = of(await turn(services, sess.id, "hello"), "usage")
    assert usage["cache.breakpoints"] == ["tools", 0, 1]
    gateway.cache_layout = "tail"
    (usage,) = of(await turn(services, sess.id, "more"), "usage")
    assert usage["cache.breakpoints"] == ["tools", 0, 3]  # last user message == last message


# ------------------------------------------------------------------ span
@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch, home: Path) -> Iterator[None]:
    pytest.importorskip("opentelemetry.sdk", reason="otel extra not installed")
    obs.reset()
    monkeypatch.setenv("AUOREO_OTEL_TEST", "1")
    assert obs.setup(Settings(home=home, otel_mode="local")) == "local"
    yield
    obs.reset()


async def test_llm_call_span_carries_the_cache_attributes(
    otel: None, telemetry: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    (usage,) = of(await turn(services, sess.id, "hello"), "usage")
    spans = [s for s in obs.memory_exporter().get_finished_spans() if s.name == "llm.call"]
    (llm,) = [s for s in spans if s.attributes["purpose"] == "turn"]
    assert llm.attributes["cache.prefix_hash"] == usage["cache.prefix_hash"]
    assert llm.attributes["tools.hash"] == usage["tools.hash"]
    assert llm.attributes["cache.breakpoints"] == ""
    assert llm.attributes["tokens.prompt"] == usage["prompt_tokens"] > 0
    assert llm.attributes["tokens.completion"] == usage["completion_tokens"]


async def test_llm_call_span_is_unchanged_with_the_flag_off(
    otel: None, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turn(services, sess.id, "hello")
    spans = [s for s in obs.memory_exporter().get_finished_spans() if s.name == "llm.call"]
    assert spans and all(not KEYS & set(s.attributes) for s in spans)
    assert all(set(s.attributes) == {"model", "purpose", "tokens.prompt", "tokens.completion"} for s in spans)


# ------------------------------------------------------------------ real gateway (stubbed Router)
async def test_litellm_gateway_reports_the_shaped_request(home: Path) -> None:
    from types import SimpleNamespace

    from auoreo.core.settings import ModelGroup
    from auoreo.gateway.gateway import LiteLLMGateway
    from auoreo.gateway.types import LLMRequest, Usage

    st = Settings(home=home, models={"default": ModelGroup(models=["anthropic/claude-opus-5-5"])})
    gw = LiteLLMGateway(st, lambda n: "sk-test" if n.endswith("_API_KEY") else None)
    gw.variants = []
    sent: list[dict[str, Any]] = []

    async def acompletion(**kw: Any) -> Any:
        sent.append(kw)

        async def chunks() -> Any:
            delta = SimpleNamespace(content="hi", tool_calls=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")], usage=None)

        return chunks()

    gw._router = SimpleNamespace(acompletion=acompletion)
    msgs = (SYSTEM, {"role": "user", "content": "hello"})
    request = LLMRequest(model="default", messages=msgs, metadata={"session_id": "s", "run_id": "r"})

    (usage,) = [i async for i in gw.stream(request) if isinstance(i, Usage)]
    assert usage.cache is None  # flag off: nothing is computed or attached

    st.m7.token.telemetry = True
    (usage,) = [i async for i in gw.stream(request) if isinstance(i, Usage)]
    assert usage.cache is not None
    assert usage.cache.breakpoints == (0, 1)  # as sent: system + last user message, no tools
    assert "cache_control" in str(sent[-1]["messages"])
    assert usage.cache.prefix_hashes == caching.prefix_hashes(msgs)  # marks do not change the hash
    assert usage.span_attrs["cache.breakpoints"] == "0,1" and usage.span_attrs["tokens.prompt"] >= 0
