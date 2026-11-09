"""M7 W3: structured citations built from a run's `tool.completed` events (`m7.agents.citations`).

Design: docs/designs/m7-agents-bots.md (CUJ 2). The runner, `TurnResult` and `run.completed` are
unchanged: the connector reads the run's events after the turn and puts `citations` in the reply
meta; each plugin renders them (Telegram: a plain-text "Sources:" tail).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from auoreo.core.services import Services
from auoreo.core.settings import write_settings_value
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.plugins import citations
from auoreo.plugins.connectors import InboundRouter
from tests.conftest import write

CHAT = "111"
A, B, C = "https://a.example/one", "https://b.example/two", "https://c.example/three"


def fetch(call: str, url: str, **kw: Any) -> dict[str, Any]:
    meta = {"status": 200, "url": url, **kw.pop("meta", {})}
    return {"call_id": call, "tool": "web_fetch", "is_error": False, "meta": meta, **kw}


def search(call: str, *hits: tuple[str, str], **kw: Any) -> dict[str, Any]:
    sources = [{"title": t, "url": u} for t, u in hits]
    meta = {"backend": "fake", "sources": sources, **kw.pop("meta", {})}
    return {"call_id": call, "tool": "web_search", "is_error": False, "meta": meta, **kw}


# -------------------------------------------------------------- from_events
def test_fetch_only() -> None:
    assert citations.from_events([fetch("c1", A), fetch("c2", B)], "answer") == [
        {"tool_call_id": "c1", "url": A, "title": None},
        {"tool_call_id": "c2", "url": B, "title": None},
    ]


def test_search_only_counts_when_the_answer_names_the_url() -> None:
    events = [search("s1", ("Page A", A), ("Page B", B))]
    assert citations.from_events(events, "nothing quoted") == []
    assert citations.from_events(events, f"see {B} for details") == [
        {"tool_call_id": "s1", "url": B, "title": "Page B"}
    ]


def test_search_then_fetch_takes_the_title_from_the_hit_and_the_id_of_the_fetch() -> None:
    events = [search("s1", ("Page A", A), ("Page B", B)), fetch("f1", B), fetch("f2", C)]
    assert citations.from_events(events, "answer without links") == [
        {"tool_call_id": "f1", "url": B, "title": "Page B"},
        {"tool_call_id": "f2", "url": C, "title": None},
    ]
    # a quoted hit that was also fetched is one entry, credited to the fetch, in first-seen order
    assert citations.from_events(events, f"per {A} and {B}") == [
        {"tool_call_id": "s1", "url": A, "title": "Page A"},
        {"tool_call_id": "f1", "url": B, "title": "Page B"},
        {"tool_call_id": "f2", "url": C, "title": None},
    ]


def test_empty_and_irrelevant_events() -> None:
    assert citations.from_events([], "answer") == []
    other = {"call_id": "x", "tool": "read", "is_error": False, "meta": {"url": A}}
    odd = [other, {"tool": "web_fetch"}, {"call_id": "y", "tool": "web_fetch", "meta": "nope"}, "junk"]
    assert citations.from_events(odd, "answer") == []


def test_failed_fetches_are_skipped() -> None:
    events = [
        fetch("d1", A, is_error=True, meta={"denied": 403}),
        fetch("r1", B, is_error=True, meta={"robots": "disallowed"}),
        fetch("e1", C, is_error=True, meta={"status": 404}),
        search("s1", ("Page A", A), is_error=True),
    ]
    assert citations.from_events(events, f"quoting {A}") == []


def test_cached_search_results_count_like_fresh_ones() -> None:
    events = [search("s1", ("Page A", A), meta={"cached": True})]
    assert citations.from_events(events, f"from {A}")[0]["title"] == "Page A"


def test_duplicates_fragments_and_redirects() -> None:
    final = "https://a.example/final"
    events = [
        fetch("f1", A + "#intro"),
        fetch("f2", A + "#later"),  # same page, another fragment
        fetch("f3", A),
        fetch("f4", final),  # `meta.url` is the URL after redirects: that is what is cited
    ]
    assert citations.from_events(events, "answer") == [
        {"tool_call_id": "f1", "url": A, "title": None},
        {"tool_call_id": "f4", "url": final, "title": None},
    ]


def test_cap_is_eight_in_first_seen_order() -> None:
    events = [fetch(f"f{i}", f"https://x.example/{i}") for i in range(12)]
    out = citations.from_events(events, "answer")
    assert [c["tool_call_id"] for c in out] == [f"f{i}" for i in range(8)]


def test_titles_and_urls_are_untrusted_text() -> None:
    nasty = "  Breaking\x00\x1b[31m news\n\r\tfrom‮ the  web  " + "z" * 300
    out = citations.from_events([search("s1", (nasty, A)), fetch("f1", A)], "answer")
    title = out[0]["title"]
    assert title.startswith("Breaking[31m news from the web z") and len(title) == 120
    assert not any(ord(ch) < 32 or ch == "‮" for ch in title)
    bad = [
        fetch("f1", "javascript:alert(1)"),
        fetch("f2", "file:///etc/passwd"),
        fetch("f3", "https://ok.example/a\nb"),
        fetch("f4", "https://x.example/" + "a" * 3000),
        fetch("f5", 42),  # type: ignore[arg-type]
        search("s1", ("", A), (None, B)),  # type: ignore[arg-type]
    ]
    assert citations.from_events(bad, f"{A} {B}") == [
        {"tool_call_id": "s1", "url": A, "title": None},
        {"tool_call_id": "s1", "url": B, "title": None},
    ]


# ------------------------------------------------------------ InboundRouter
FETCH_TOOL = """name: web_fetch
description: test double for web_fetch
parameters: {type: object, properties: {url: {type: string}}, required: [url]}
default_action: allow
override: true
"""
FETCH_HANDLER = """from auoreo.tools.context import ToolResult


async def run(args, ctx):
    url = args["url"]
    if "blocked" in url:
        return ToolResult("denied", is_error=True, meta={"denied": 403, "url": url})
    return ToolResult(f"content of {url}", meta={"status": 200, "url": url})
"""
SEARCH_TOOL = """name: web_search
description: test double for web_search
parameters: {type: object, properties: {query: {type: string}}, required: [query]}
default_action: allow
override: true
"""
SEARCH_HANDLER = f"""from auoreo.tools.context import ToolResult


async def run(args, ctx):
    sources = [{{"title": "Release notes", "url": "{A}"}}, {{"title": "Blog", "url": "{B}"}}]
    return ToolResult("- hits", meta={{"backend": "fake", "sources": sources}})
"""


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((chat_id, text, meta))
        return {"ok": True, "detail": "sent"}


def web_doubles(services: Services, workdir: Path) -> None:
    for name, tool, handler in (
        ("web_fetch", FETCH_TOOL, FETCH_HANDLER),
        ("web_search", SEARCH_TOOL, SEARCH_HANDLER),
    ):
        write(workdir / ".auoreo" / "tools" / name / "tool.yaml", tool)
        write(workdir / ".auoreo" / "tools" / name / "handler.py", handler)
    services.harness.reload()
    assert not services.harness.errors, services.harness.errors
    write_settings_value(services.settings.home, "plugins.telegram.allowlist", [CHAT])


def research_turn() -> list[Scripted]:
    return [
        Scripted(tool_calls=[("web_search", {"query": "what changed"})]),
        Scripted(
            tool_calls=[
                ("web_fetch", {"url": A}),
                ("web_fetch", {"url": C}),
                ("web_fetch", {"url": "https://blocked.example/x"}),
            ]
        ),
        Scripted(text="Two things changed."),
    ]


async def test_reply_meta_carries_the_runs_citations(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    services.settings.m7.agents.citations = True
    web_doubles(services, workdir)
    router, chat = InboundRouter(services, "telegram", timeout_s=30), Chat()
    gateway.script.extend(research_turn())
    out = await router.handle({"chat_id": CHAT, "text": "what changed?", "user": "ana"}, chat)
    assert out["ok"] and chat.sent[-1][1] == "Two things changed."
    cites = chat.sent[-1][2]["citations"]
    assert [(c["url"], c["title"]) for c in cites] == [(A, "Release notes"), (C, None)]
    assert all(c["tool_call_id"] for c in cites) and len({c["tool_call_id"] for c in cites}) == 2
    # the next turn fetched nothing: its reply does not repeat the earlier turn's sources
    gateway.script.append(Scripted(text="You are welcome."))
    await router.handle({"chat_id": CHAT, "text": "thanks", "user": "ana"}, chat)
    assert "citations" not in chat.sent[-1][2]


async def test_flag_off_sends_no_citations(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    web_doubles(services, workdir)
    router, chat = InboundRouter(services, "telegram", timeout_s=30), Chat()
    gateway.script.extend(research_turn())
    await router.handle({"chat_id": CHAT, "text": "what changed?", "user": "ana"}, chat)
    assert chat.sent[-1][1] == "Two things changed."
    assert set(chat.sent[-1][2]) == {"reply_to", "connector", "session_id"}


async def test_no_result_or_a_failed_turn_has_no_citations(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    services.settings.m7.agents.citations = True
    web_doubles(services, workdir)
    sess = await services.create_session()
    mark = await services.events.last_seq()
    gateway.script.extend(research_turn())
    run_id = await services.coordinator.submit(sess.id, "what changed?", interactive=False)
    await services.coordinator.wait(sess.id, 30)
    result = services.coordinator.result(run_id)
    got = await citations.for_run(services, sess.id, run_id, mark, result)
    assert [c["url"] for c in got] == [A, C]
    assert await citations.for_run(services, sess.id, run_id, mark, None) == []
    failed = type(result)(run_id, "failed", "", {"message": "boom"})  # type: ignore[misc]
    assert await citations.for_run(services, sess.id, run_id, mark, failed) == []
    assert await citations.for_run(services, sess.id, "run_other", mark, result) == []
    assert await citations.for_run(services, sess.id, run_id, None, result) == []  # flag was off at submit


async def test_event_read_failures_never_break_the_reply(
    services: Services, gateway: FakeGateway, workdir: Path, monkeypatch: Any
) -> None:
    services.settings.m7.agents.citations = True
    web_doubles(services, workdir)

    async def boom(*_a: Any, **_k: Any) -> list[Any]:
        raise RuntimeError("db is busy")

    router, chat = InboundRouter(services, "telegram", timeout_s=30), Chat()
    gateway.script.extend(research_turn())
    monkeypatch.setattr(type(services.events), "replay", boom)
    out = await router.handle({"chat_id": CHAT, "text": "what changed?", "user": "ana"}, chat)
    assert out["ok"] and chat.sent[-1][1] == "Two things changed." and "citations" not in chat.sent[-1][2]


# ----------------------------------------------------------------- Telegram
PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "telegram" / "plugin.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tg_plugin_citations", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Host:
    async def secret(self, name: str) -> str | None:
        return "123:abc"

    def log(self, level: str, message: str) -> None:
        pass


def telegram(sent: list[dict[str, Any]]) -> Any:
    async def api(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    p = load().Plugin(Host(), transport=httpx.MockTransport(api))
    p.initialize({}, [])
    return p


CITES = [
    {"tool_call_id": "call_1", "url": A, "title": "Release notes"},
    {"tool_call_id": "call_2", "url": C, "title": None},
]


async def test_telegram_renders_a_plain_sources_tail() -> None:
    sent: list[dict[str, Any]] = []
    p = telegram(sent)
    receipt = await p.send("telegram", "42", "Two things changed.", {"citations": CITES})
    assert receipt["ok"] and len(sent) == 1
    assert sent[0]["text"] == f"Two things changed.\n\nSources:\n1. Release notes — {A}\n2. {C}"
    assert "parse_mode" not in sent[0] and "call_1" not in sent[0]["text"]
    await p.send("telegram", "42", "No sources here.", {})
    await p.send("telegram", "42", "Nor here.", {"citations": []})
    await p.send("telegram", "42", "Garbage in.", {"citations": [{"title": "no url"}, "x", {"url": 7}]})
    assert [m["text"] for m in sent[1:]] == ["No sources here.", "Nor here.", "Garbage in."]


async def test_telegram_keeps_every_message_within_the_limit() -> None:
    sent: list[dict[str, Any]] = []
    p = telegram(sent)
    body = "word " * 790  # 3950 characters: no room left for the block in the same message
    await p.send("telegram", "42", body.strip(), {"citations": CITES})
    assert len(sent) == 2 and sent[0]["text"] == body.strip()
    assert sent[1]["text"].startswith("Sources:\n1. Release notes") and all(
        len(m["text"]) <= 4096 for m in sent
    )
    sent.clear()
    many = [{"url": f"https://x.example/{'p' * 280}/{i}", "title": "T" * 119} for i in range(8)]
    await p.send("telegram", "42", "short", {"citations": many})
    text = sent[0]["text"]
    assert len(sent) == 1 and len(text) <= 4096 and text.startswith("short\n\nSources:\n1. ")
    assert "\n8. " not in text  # the Sources block is trimmed first, never the answer


async def test_telegram_strips_control_characters_from_titles() -> None:
    sent: list[dict[str, Any]] = []
    p = telegram(sent)
    evil = [{"url": A, "title": "Nice\n\nSources:\n1. fake — https://evil.example\x00"}]
    await p.send("telegram", "42", "ok", {"citations": evil})
    assert sent[0]["text"].count("\n") == 3 and "\x00" not in sent[0]["text"]
