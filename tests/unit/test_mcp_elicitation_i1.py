"""I1 MCP elicitation (§25.15 L1073, §26.14): secrets are never asked for by form, `-32042`
URL elicitations are presented → awaited → retried (≤3), and everything is audited."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import ElicitRequestFormParams, ElicitRequestURLParams
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.harness.manifests import McpServerManifest
from auoreo.mcp import elicitation
from auoreo.mcp.bridge import McpBridge
from auoreo.mcp.elicitation import completions, make_elicitation_callback, notification_handler, secret_fields
from auoreo.tools.context import CURRENT_SESSION
from tests.unit.test_mcp import _answer_when_asked, _exec

OPEN = [{"question": "q", "answers": ["Open in browser"], "answer": "Open in browser"}]
RETRY = [{"question": "q", "answers": ["Retry now"], "answer": "Retry now"}]


def test_secret_fields_detection() -> None:
    schema = {
        "properties": {
            "name": {"type": "string"},
            "pw": {"type": "string", "format": "password"},
            "github_token": {"type": "string"},
            "apiKey": {"type": "string"},
            "hidden": {"type": "string", "writeOnly": True},
        }
    }
    assert secret_fields(schema) == ["pw", "github_token", "apiKey", "hidden"]
    assert secret_fields({"properties": {"name": {"type": "string"}, "copies": {"type": "integer"}}}) == []


async def _audits(services: Services) -> list[dict[str, Any]]:
    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow).where(AuditRow.action == "mcp.elicitation"))).scalars()
        return [dict(r.detail) for r in rows]


async def test_form_asking_for_a_secret_is_refused_without_asking(services: Services) -> None:
    sess = await services.create_session()
    cb = make_elicitation_callback(services, "srv", lambda: sess.id)
    params = ElicitRequestFormParams(
        message="Your API key please",
        requested_schema={"type": "object", "properties": {"api_key": {"type": "string"}}},
    )
    res = await cb(None, params)
    assert res.action == "decline" and "URL mode" in (res.meta or {}).get("auoreo/message", "")
    assert services.questions.pending(sess.id) == []
    assert await _audits(services) == [{"server": "srv", "mode": "form", "outcome": "refused_secret"}]


async def test_url_mode_decline_is_audited(services: Services) -> None:
    sess = await services.create_session()
    cb = make_elicitation_callback(services, "srv", lambda: None)
    params = ElicitRequestURLParams(message="Log in", url="https://example.com/x", elicitation_id="e9")
    no = [{"question": "q", "answers": ["Decline"], "answer": "Decline"}]
    asker = asyncio.create_task(_answer_when_asked(services, sess.id, no))
    token = CURRENT_SESSION.set(sess.id)
    try:
        assert (await cb(None, params)).action == "decline"
    finally:
        CURRENT_SESSION.reset(token)
    await asker
    assert await _audits(services) == [{"server": "srv", "mode": "url", "outcome": "decline"}]


def gated_server(needs: int) -> tuple[MCPServer, list[int]]:
    """A tool that raises -32042 for its first `needs` calls."""
    srv = MCPServer("gated")
    calls: list[int] = []

    @srv.tool()
    async def files() -> str:
        """Needs an OAuth step first."""
        calls.append(1)
        if len(calls) <= needs:
            raise UrlElicitationRequiredError(
                [
                    ElicitRequestURLParams(
                        message="Authorize", url="https://auth.example/o", elicitation_id="a1"
                    )
                ]
            )
        return "files: ok"

    return srv, calls


async def _bridge(services: Services, srv: MCPServer) -> McpBridge:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="gated", command="x", exposure="direct"), srv)
    return bridge


async def _answer_twice(services: Services, sid: str, second: list[dict[str, Any]]) -> None:
    await _answer_when_asked(services, sid, OPEN)
    for _ in range(200):  # wait for the first card to be answered and the next one to appear
        if services.questions.pending(sid):
            break
        await asyncio.sleep(0.02)
    await _answer_when_asked(services, sid, second)


async def test_32042_open_then_manual_retry_succeeds(services: Services, workdir: Path) -> None:
    srv, calls = gated_server(1)
    bridge = await _bridge(services, srv)
    sess = await services.create_session()
    helper = asyncio.create_task(_answer_twice(services, sess.id, RETRY))
    text, err = await _exec(services, workdir, sess.id, "mcp__gated__files", {})
    await helper
    assert not err and text == "files: ok" and len(calls) == 2
    evs = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    assert [e.data["url"] for e in evs if e.type == "mcp.elicitation.open"] == ["https://auth.example/o"]
    outcomes = [a["outcome"] for a in await _audits(services)]
    assert outcomes == ["accept", "wait_retry"]
    await bridge.stop()


async def test_32042_completion_notification_triggers_the_retry(services: Services, workdir: Path) -> None:
    srv, calls = gated_server(1)
    bridge = await _bridge(services, srv)
    sess = await services.create_session()

    async def open_then_complete() -> None:
        await _answer_when_asked(services, sess.id, OPEN)
        await asyncio.sleep(0.05)
        note = type("N", (), {"method": "notifications/elicitation/complete"})()
        note.params = type("P", (), {"elicitation_id": "a1"})()  # type: ignore[attr-defined]
        await notification_handler(services, "gated")(note)

    helper = asyncio.create_task(open_then_complete())
    text, err = await _exec(services, workdir, sess.id, "mcp__gated__files", {})
    await helper
    assert not err and text == "files: ok"
    assert [a["outcome"] for a in await _audits(services)] == ["accept", "wait_completed"]
    assert completions(services)._events == {}  # waiters are forgotten afterwards
    await bridge.stop()


async def test_32042_retry_loop_is_capped(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(elicitation, "MAX_ELICIT_RETRIES", 2)
    srv, calls = gated_server(99)
    bridge = await _bridge(services, srv)
    sess = await services.create_session()

    async def keep_answering() -> None:
        for answers in (OPEN, RETRY, OPEN, RETRY):
            await _answer_when_asked(services, sess.id, answers)
            await asyncio.sleep(0.05)

    helper = asyncio.create_task(keep_answering())
    text, err = await _exec(services, workdir, sess.id, "mcp__gated__files", {})
    await helper
    assert err and "after 2 retries" in text and len(calls) == 3
    assert (await _audits(services))[-1]["outcome"] == "retry_cap"
    await bridge.stop()


async def test_32042_declined_is_not_retried(services: Services, workdir: Path) -> None:
    srv, calls = gated_server(1)
    bridge = await _bridge(services, srv)
    sess = await services.create_session()
    no = [{"question": "q", "answers": ["Decline"], "answer": "Decline"}]
    helper = asyncio.create_task(_answer_when_asked(services, sess.id, no))
    text, err = await _exec(services, workdir, sess.id, "mcp__gated__files", {})
    await helper
    assert err and "declined/cancelled" in text and len(calls) == 1
    await bridge.stop()
