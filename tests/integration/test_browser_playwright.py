"""Real headless Chromium (playwright backend) against a local http.server fixture site.

Needs the `playwright` package and a matching Chromium build, found through
`PLAYWRIGHT_BROWSERS_PATH` (defaults here to `/opt/pw-browsers` when that exists) or
`settings.browser_executable_path`. Skips with the launch error otherwise.
"""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright")

from auoreo.backends.browser import BrowserBackend  # noqa: E402
from auoreo.core.services import Services  # noqa: E402
from auoreo.core.settings import Settings  # noqa: E402
from auoreo.gateway.fake import FakeGateway  # noqa: E402
from auoreo.tools.context import ToolContext, ToolError, ToolResult  # noqa: E402
from auoreo.tools.net import check_url  # noqa: E402

INDEX = b"""<!doctype html><html><head><title>Fixture</title></head><body>
<h1>Fixture site</h1>
<a href="/second">Go to second</a>
<form action="/result" method="get">
  <label for="q">Query</label><input id="q" name="q">
  <button type="submit">Send</button>
</form>
</body></html>"""
SECOND = b"<!doctype html><title>Second</title><h1>Second page</h1><a href='/'>Home</a>"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        port = self.server.server_address[1]
        if parts.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/second")
            self.end_headers()
            return
        if parts.path == "/result":
            q = parse_qs(parts.query).get("q", [""])[0]
            body = f"<!doctype html><title>Result</title><p id='out'>You searched: {q}</p>".encode()
        else:
            body = {"/": INDEX, "/second": SECOND}.get(parts.path, b"not found")
        self.send_response(200 if body != b"not found" else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _read(path: str) -> bytes:
    return Path(path).read_bytes()


@pytest.fixture
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _browsers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and Path("/opt/pw-browsers").is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")


async def _make(home: Path, workdir: Path, allow_private: bool) -> tuple[Services, BrowserBackend]:
    settings = Settings(
        home=home,
        gateway="fake",
        backends={"browser": "playwright"},
        allow_private_urls=allow_private,
    )
    svc = await Services.create(
        settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )
    browser = svc.backend("browser")
    assert isinstance(browser, BrowserBackend)
    status = await browser.check()
    if not status.ok:
        await svc.close()
        pytest.skip(f"Chromium cannot launch: {status.detail} ({status.remedy})")
    return svc, browser


@pytest.fixture
async def chromium(home: Path, workdir: Path) -> AsyncIterator[tuple[Services, BrowserBackend]]:
    svc, browser = await _make(home, workdir, allow_private=True)
    try:
        yield svc, browser
    finally:
        await svc.close()


async def test_check_reports_version(chromium: tuple[Services, BrowserBackend]) -> None:
    _, browser = chromium
    status = await browser.check()
    assert status.ok and "Chromium" in status.detail


async def test_navigate_click_type_submit_screenshot(
    chromium: tuple[Services, BrowserBackend], site: str
) -> None:
    svc, browser = chromium
    snap = await browser.navigate("s1", site + "/")
    text = snap.render()
    assert snap.title == "Fixture" and "Fixture site" in text
    assert "[e1] link 'Go to second'" in text
    roles = {r["role"] for r in snap.refs.values()}
    assert {"link", "textbox", "button"} <= roles

    page = await browser.page("s1")
    ua = await page.evaluate("navigator.userAgent")
    assert ua.endswith("auoreo/0.1")
    assert page.viewport_size == {"width": 1280, "height": 800}

    snap = await browser.click("s1", ref="e1")
    assert snap.url == site + "/second" and snap.title == "Second"
    snap = await browser.back("s1")
    assert snap.title == "Fixture"

    box = next(ref for ref, info in snap.refs.items() if info["role"] == "textbox")
    assert snap.refs[box]["name"] == "Query"
    snap = await browser.type("s1", "hello otters", ref=box, submit=True)
    assert snap.url.startswith(site + "/result") and "You searched: hello otters" in snap.text

    ctx = ToolContext(session_id="s1", cwd=svc.cwd, home=svc.settings.home, services={"services": svc})
    result = await svc.harness.tools["browser_screenshot"].handler()({"full_page": True}, ctx)
    assert isinstance(result, ToolResult) and not result.is_error
    assert _read(result.meta["path"]).startswith(b"\x89PNG")
    assert (await browser.screenshot("s1")).startswith(b"\x89PNG")

    assert await browser.close_session("s1") and not browser.has_page("s1")


async def test_redirect_to_private_address_refused(
    home: Path, workdir: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the SSRF guard on, `127.0.0.1` stands in for a public host and `localhost`
    (which resolves to loopback) is the private redirect target."""

    async def guard(url: str, allow_private: bool = False) -> None:
        if urlsplit(url).hostname == "127.0.0.1":
            return
        await check_url(url, allow_private)

    monkeypatch.setattr("auoreo.backends.browser.check_url", guard)
    svc, browser = await _make(home, workdir, allow_private=False)
    try:
        snap = await browser.navigate("s1", site + "/")
        assert snap.title == "Fixture"
        with pytest.raises(ToolError, match="landed on a blocked URL"):
            await browser.navigate("s1", site + "/redirect-private")
        page = await browser.page("s1")
        assert page.url == "about:blank"
        with pytest.raises(ToolError, match="SSRF"):
            await browser.navigate("s1", site.replace("127.0.0.1", "localhost") + "/")
        with pytest.raises(ToolError, match="blocked"):
            await browser.navigate("s1", "file:///etc/passwd")
    finally:
        await svc.close()
