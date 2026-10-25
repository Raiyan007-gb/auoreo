"""Browser engine logic (policy, refs, lifecycle) against the in-memory fake backend."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auoreo.backends.browser import BrowserBackend, Snapshot
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.sessions_ops import delete_session
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext, ToolError

PUBLIC = "http://93.184.215.14/"  # IP literal: resolves offline, not private
LOCAL = "http://127.0.0.1:8000/"

HOME_HTML = """<html><head><title>Fixture home</title></head><body>
<h1>Welcome</h1><p>Some intro text.</p>
<a href="/second">Second page</a>
<form action="/search" method="get">
  <input name="q" aria-label="Query">
  <select name="lang"><option value="en">English</option><option value="fr">French</option></select>
  <input type="checkbox" name="exact" aria-label="Exact">
  <button type="submit">Search</button>
</form>
</body></html>"""


def _settings(home: Path, allow_private: bool) -> Settings:
    return Settings(
        home=home,
        gateway="fake",
        approval_timeout_s=5,
        backends={"browser": "fake"},
        allow_private_urls=allow_private,
    )


async def _services(home: Path, workdir: Path, allow_private: bool) -> Services:
    return await Services.create(
        _settings(home, allow_private), cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )


@pytest.fixture
async def svc(home: Path, workdir: Path) -> AsyncIterator[Services]:
    s = await _services(home, workdir, allow_private=False)
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def private_svc(home: Path, workdir: Path) -> AsyncIterator[Services]:
    s = await _services(home, workdir, allow_private=True)
    try:
        yield s
    finally:
        await s.close()


def fake(s: Services) -> BrowserBackend:
    inst = s.backend("browser")
    assert isinstance(inst, BrowserBackend) and type(inst).__module__.endswith("fake")
    inst.pages[PUBLIC] = HOME_HTML  # type: ignore[attr-defined]
    inst.pages[LOCAL] = HOME_HTML  # type: ignore[attr-defined]
    return inst


async def call(
    s: Services, name: str, args: dict, *, session: str = "s1", rules: list[Rule] | None = None
) -> tuple[str, bool]:
    allow_all = [Rule(permission="*", pattern="*", action="allow")]
    engine = PermissionEngine(rules if rules is not None else allow_all)
    ctx = ToolContext(session_id=session, cwd=s.cwd, home=s.settings.home, services={"services": s})
    out = await s.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)), tools=s.harness.tools, ctx=ctx, engine=engine
    )
    return out.result.content, out.result.is_error


async def test_navigate_returns_snapshot_with_stable_refs(svc: Services) -> None:
    b = fake(svc)
    text, err = await call(svc, "browser_navigate", {"url": PUBLIC})
    assert not err, text
    assert "Page: Fixture home" in text and "Welcome" in text
    assert "[e1] link 'Second page'" in text and "[e2] textbox 'Query'" in text
    assert "[e3] combobox" in text and "[e4] checkbox 'Exact'" in text and "[e5] button 'Search'" in text
    first = await b.snapshot("s1")
    again = await b.snapshot("s1")
    assert first.refs == again.refs and first.refs["e1"]["selector"] == '[data-auoreo-ref="e1"]'
    assert set(first.refs["e2"]) >= {"role", "name", "selector"}


async def test_private_and_non_web_urls_blocked(svc: Services) -> None:
    fake(svc)
    text, err = await call(svc, "browser_navigate", {"url": LOCAL})
    assert err and "SSRF" in text
    for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,<b>x</b>"):
        text, err = await call(svc, "browser_navigate", {"url": url})
        assert err and "blocked" in text, url
    assert svc.backend("browser").navigations == []


async def test_private_allowed_with_setting(private_svc: Services) -> None:
    fake(private_svc)
    text, err = await call(private_svc, "browser_navigate", {"url": LOCAL})
    assert not err and "Fixture home" in text
    text, err = await call(private_svc, "browser_navigate", {"url": "file:///etc/passwd"})
    assert err and "'file:' navigations are blocked" in text


async def test_redirect_and_link_landing_rechecked(svc: Services) -> None:
    b = fake(svc)
    b.redirects[PUBLIC + "go"] = "http://169.254.169.254/latest/meta-data"  # type: ignore[attr-defined]
    text, err = await call(svc, "browser_navigate", {"url": PUBLIC + "go"})
    assert err and "landed on a blocked URL" in text
    page = b._get_page("s1")
    assert page is not None and page.url == "about:blank"
    b.pages[PUBLIC + "evil"] = '<a href="http://10.0.0.5/admin">admin</a>'  # type: ignore[attr-defined]
    await call(svc, "browser_navigate", {"url": PUBLIC + "evil"})
    text, err = await call(svc, "browser_click", {"ref": "e1"})
    assert err and "10.0.0.5" in text


async def test_click_type_select_update_fake_state(svc: Services) -> None:
    b = fake(svc)
    b.pages[PUBLIC + "second"] = "<title>Second</title><p>Page two</p>"  # type: ignore[attr-defined]
    await call(svc, "browser_navigate", {"url": PUBLIC})
    text, err = await call(svc, "browser_click", {"ref": "e1"})
    assert not err and "URL: http://93.184.215.14/second" in text and "Page two" in text
    text, err = await call(svc, "browser_back", {})
    assert not err and "Fixture home" in text
    text, err = await call(svc, "browser_type", {"ref": "e2", "text": "otters"})
    assert not err and "[e2] textbox 'Query' = 'otters'" in text
    text, err = await call(svc, "browser_select", {"ref": "e3", "value": "French"})
    assert not err and "= 'fr'" in text
    text, err = await call(svc, "browser_select", {"ref": "e3", "value": "Klingon"})
    assert err and "no option" in text
    await call(svc, "browser_click", {"ref": "e4"})
    text, err = await call(svc, "browser_click", {"ref": "e5"})
    assert not err and "URL: http://93.184.215.14/search?q=otters&lang=fr&exact=" in text
    page = b._get_page("s1")
    assert page is not None and ('[data-auoreo-ref="e2"]', "otters") in page.typed
    assert page.clicks[0] == '[data-auoreo-ref="e1"]'


async def test_type_submit_and_selector(svc: Services) -> None:
    fake(svc)
    await call(svc, "browser_navigate", {"url": PUBLIC})
    text, err = await call(svc, "browser_type", {"selector": "[name=q]", "text": "hi", "submit": True})
    assert not err and "search?q=hi&lang=en" in text
    text, err = await call(svc, "browser_click", {"ref": "e99"})
    assert err and "unknown ref" in text


async def test_tools_refuse_without_open_page(svc: Services) -> None:
    fake(svc)
    cases = {
        "browser_snapshot": {},
        "browser_click": {"ref": "e1"},
        "browser_type": {"ref": "e1", "text": "x"},
        "browser_select": {"ref": "e1", "value": "x"},
        "browser_back": {},
        "browser_screenshot": {},
    }
    for name, args in cases.items():
        text, err = await call(svc, name, args)
        assert err and "no browser page is open" in text, name


async def test_screenshot_saved_and_close(svc: Services) -> None:
    b = fake(svc)
    await call(svc, "browser_navigate", {"url": PUBLIC})
    text, err = await call(svc, "browser_screenshot", {"full_page": True})
    assert not err and "screenshot saved" in text
    shots = list((svc.settings.home / "sessions" / "s1" / "screenshots").glob("*.png"))
    assert len(shots) == 1 and shots[0].read_bytes().startswith(b"\x89PNG")
    text, _ = await call(svc, "browser_close", {})
    assert text == "browser closed" and not b.has_page("s1")
    text, _ = await call(svc, "browser_close", {})
    assert text == "no browser was open for this session"


async def test_sessions_are_isolated(svc: Services) -> None:
    b = fake(svc)
    await call(svc, "browser_navigate", {"url": PUBLIC}, session="a")
    assert b.has_page("a") and not b.has_page("b")
    text, err = await call(svc, "browser_snapshot", {}, session="b")
    assert err


async def test_navigate_needs_approval_by_default(svc: Services) -> None:
    fake(svc)
    text, err = await call(svc, "browser_navigate", {"url": PUBLIC}, rules=[])
    assert err and "needs approval" in text
    rule = Rule(permission="webfetch", pattern=PUBLIC + "*", action="allow")
    text, err = await call(svc, "browser_navigate", {"url": PUBLIC}, rules=[rule])
    assert not err
    text, err = await call(svc, "browser_snapshot", {}, rules=[])  # default allow
    assert not err


async def test_session_delete_closes_browser(svc: Services) -> None:
    b = fake(svc)
    row = await svc.create_session()
    await call(svc, "browser_navigate", {"url": PUBLIC}, session=row.id)
    assert b.has_page(row.id)
    await delete_session(svc, row.id)
    assert not b.has_page(row.id)
    await svc.close_backend_session(row.id)  # idempotent


async def test_shutdown_closes_all(home: Path, workdir: Path) -> None:
    s = await _services(home, workdir, allow_private=False)
    b = fake(s)
    await b.navigate("x", PUBLIC)
    await s.close()
    assert not b.has_page("x")


def test_snapshot_render_truncates() -> None:
    snap = Snapshot("http://h/", "T", "x" * 5000, {"e1": {"role": "button", "name": "Go", "selector": "s"}})
    out = snap.render(max_chars=1000)
    assert "chars truncated" in out and "[e1] button 'Go'" in out
    assert "No interactive elements" in Snapshot("u", "", "", {}).render()


async def test_wait_validation(svc: Services) -> None:
    b = fake(svc)
    with pytest.raises(ToolError):
        await b.navigate("s1", PUBLIC, wait="never")
