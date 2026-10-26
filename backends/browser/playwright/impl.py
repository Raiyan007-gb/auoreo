"""Headless Chromium through playwright-python (one BrowserContext + page per session).

Browser binary: `settings.browser_executable_path` if set, else Playwright's own lookup,
which honours `PLAYWRIGHT_BROWSERS_PATH` (e.g. `/opt/pw-browsers`). The Python package
version must match the installed browser build (`playwright==1.63.*` ↔ chromium-1243).

Besides the navigation policy in `BrowserBackend`, every sub-request the page makes is
routed through the SSRF check (host verdicts cached), so a public page cannot make the
browser fetch from a private address. Playwright does not route redirect hops, which is
why the landing URL is re-checked after each navigation.

I8: with `i8.browser.trace` each session's BrowserContext records a Playwright trace
(screenshots + DOM snapshots) saved on close as `<home>/sessions/<id>/browser-trace-<ts>.zip`
(open it with `playwright show-trace`). Traces can contain page content: they stay local.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from auoreo.backends import BackendStatus
from auoreo.backends.browser import (
    REF_ATTR,
    USER_AGENT_SUFFIX,
    VIEWPORT,
    BrowserBackend,
    Snapshot,
    check_navigation,
)
from auoreo.core.settings_i8 import i8_of
from auoreo.tools.context import ToolError
from auoreo.tools.egress import revoke_session, session_proxy

REMEDY = "set PLAYWRIGHT_BROWSERS_PATH or settings.browser_executable_path"
ACTION_TIMEOUT_MS = 10_000
NAV_TIMEOUT_MS = 30_000

# Tags interactive elements in document order and describes them (role, accessible name).
_TAG_JS = """
(attr) => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link],'
    + ' [role=checkbox], [role=radio], [role=textbox], [role=combobox], [role=menuitem], [role=tab],'
    + ' [contenteditable=""], [contenteditable=true]';
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const roleOf = (el) => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'select') return 'combobox';
    if (t === 'button') return 'button';
    if (t === 'textarea' || el.isContentEditable) return 'textbox';
    const k = (el.getAttribute('type') || 'text').toLowerCase();
    if (k === 'checkbox' || k === 'radio') return k;
    return ['submit', 'button', 'reset', 'image'].includes(k) ? 'button' : 'textbox';
  };
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const lab = by ? by.split(/\\s+/).map(i => document.getElementById(i)?.innerText || '').join(' ') : '';
    const lbl = el.labels && el.labels.length ? el.labels[0].innerText : '';
    return (el.getAttribute('aria-label') || lab || lbl || el.innerText || el.getAttribute('alt')
      || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name')
      || (el.tagName === 'INPUT' ? el.value : '') || '').replace(/\\s+/g, ' ').trim().slice(0, 100);
  };
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const box = el.getBoundingClientRect(); const st = getComputedStyle(el);
    const hidden = st.visibility === 'hidden' || st.display === 'none';
    if ((box.width === 0 && box.height === 0) || hidden) continue;
    const ref = 'e' + (++n); el.setAttribute(attr, ref);
    const item = {ref, role: roleOf(el), name: nameOf(el)};
    const role = item.role;
    if (role === 'checkbox' || role === 'radio') item.checked = !!el.checked;
    else if ((role === 'textbox' || role === 'combobox') && el.value)
      item.value = String(el.value).slice(0, 200);
    out.push(item);
  }
  return out;
}
"""


class Backend(BrowserBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._pw: Any = None
        self._browser: Any = None
        self._user_agent: str | None = None
        self._start_lock = asyncio.Lock()
        self._pages: dict[str, tuple[Any, Any]] = {}  # session -> (context, page)
        self._host_ok: dict[str, bool] = {}
        self._traced: set[str] = set()
        self.traces: list[Path] = []
        self._proxy_tokens: dict[str, str] = {}  # session -> egress proxy token (I9)

    # ------------------------------------------------------------- launching
    def _launch_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"headless": True}
        exe = getattr(self.settings, "browser_executable_path", None)
        if exe:
            kwargs["executable_path"] = str(exe)
        return kwargs

    async def _ensure_browser(self) -> Any:
        async with self._start_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ToolError("playwright is not installed (uv add playwright)") from exc
            if self._pw is None:
                self._pw = await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.launch(**self._launch_kwargs())
            except Exception as exc:
                raise ToolError(f"cannot launch Chromium ({str(exc).splitlines()[0]}); {REMEDY}") from exc
            self._user_agent = await self._derive_user_agent(self._browser)
            return self._browser

    @staticmethod
    async def _derive_user_agent(browser: Any) -> str:
        try:
            cdp = await browser.new_browser_cdp_session()
            info = await cdp.send("Browser.getVersion")
            await cdp.detach()
            base = str(info["userAgent"]).replace("HeadlessChrome/", "Chrome/")
        except Exception:
            base = f"Mozilla/5.0 (X11; Linux x86_64) Chrome/{browser.version} Safari/537.36"
        return f"{base} {USER_AGENT_SUFFIX}"

    async def check(self) -> BackendStatus:
        if self._browser is not None and self._browser.is_connected():
            return BackendStatus(True, f"Chromium {self._browser.version} (running)")
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return BackendStatus(False, "playwright is not installed", "uv add 'playwright==1.63.*'")
        try:
            async with async_playwright() as pw:
                browser = await asyncio.wait_for(pw.chromium.launch(**self._launch_kwargs()), 5)
                version = browser.version
                await browser.close()
        except Exception as exc:
            first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            return BackendStatus(False, f"cannot launch Chromium: {first}", REMEDY)
        return BackendStatus(True, f"Chromium {version} (headless)")

    # -------------------------------------------------------------- sessions
    async def _open_page(self, session_id: str) -> Any:
        browser = await self._ensure_browser()
        extra: dict[str, Any] = {}
        routed = session_proxy(f"browser:{session_id}")  # I9: browser_* tools are `net: proxy`
        if routed is not None:
            extra["proxy"], self._proxy_tokens[session_id] = routed
        context = await browser.new_context(
            viewport=dict(VIEWPORT), user_agent=self._user_agent, accept_downloads=False, **extra
        )
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        if not self.allow_private:
            await context.route("**/*", self._route)
        await self._start_trace(session_id, context)
        page = await context.new_page()
        self._pages[session_id] = (context, page)
        return page

    def trace_path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "_-") or "anon"
        return Path(self.settings.home) / "sessions" / safe / f"browser-trace-{int(time.time() * 1000)}.zip"

    async def _start_trace(self, session_id: str, context: Any) -> None:
        if not i8_of(self.settings).browser.trace:
            return
        await context.tracing.start(screenshots=True, snapshots=True, sources=False)
        self._traced.add(session_id)

    async def _stop_trace(self, session_id: str, context: Any) -> None:
        if session_id not in self._traced:
            return
        self._traced.discard(session_id)
        path = self.trace_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            await context.tracing.stop(path=str(path))
            self.traces.append(path)

    def _get_page(self, session_id: str) -> Any | None:
        entry = self._pages.get(session_id)
        if entry is None:
            return None
        if entry[1].is_closed():
            self._pages.pop(session_id, None)
            return None
        return entry[1]

    async def _close_page(self, session_id: str) -> None:
        entry = self._pages.pop(session_id, None)
        if (token := self._proxy_tokens.pop(session_id, None)) is not None:
            revoke_session(token)
        if entry is not None:
            await self._stop_trace(session_id, entry[0])
            with contextlib.suppress(Exception):
                await entry[0].close()

    async def _close_all(self) -> None:
        for sid in list(self._pages):
            await self._close_page(sid)
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._browser = self._pw = None

    async def _route(self, route: Any) -> None:
        """Abort sub-requests to private hosts or non-web schemes (SSRF guard)."""
        url = route.request.url
        parts = urlsplit(url)
        if parts.scheme in {"data", "blob", "about"}:
            await route.continue_()
            return
        key = f"{parts.scheme}://{parts.hostname}:{parts.port or ''}"
        ok = self._host_ok.get(key)
        if ok is None:
            try:
                await check_navigation(f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/", False)
                ok = True
            except ToolError:
                ok = False
            if len(self._host_ok) > 1024:
                self._host_ok.clear()
            self._host_ok[key] = ok
        await (route.continue_() if ok else route.abort("blockedbyclient"))

    # ------------------------------------------------------------ primitives
    async def _goto(self, page: Any, url: str, wait: str) -> None:
        await page.goto(url, wait_until=wait)

    async def _current_url(self, page: Any) -> str:
        return str(page.url)

    @staticmethod
    async def _settle(page: Any) -> None:
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)

    async def _click(self, page: Any, selector: str) -> None:
        await page.click(selector)
        await self._settle(page)

    async def _type(self, page: Any, selector: str, text: str, submit: bool) -> None:
        await page.fill(selector, text)
        if submit:
            await page.press(selector, "Enter")
            await self._settle(page)

    async def _select(self, page: Any, selector: str, value: str) -> list[str]:
        try:
            chosen: list[str] = await page.select_option(selector, value=value, timeout=ACTION_TIMEOUT_MS)
        except Exception:
            chosen = await page.select_option(selector, label=value, timeout=ACTION_TIMEOUT_MS)
        return chosen

    async def _screenshot(self, page: Any, full_page: bool) -> bytes:
        data: bytes = await page.screenshot(full_page=full_page, type="png")
        return data

    async def _back(self, page: Any) -> None:
        before = page.url
        response = await page.go_back(wait_until="load")
        if response is None and page.url == before:
            raise ToolError("no previous page in this session's history")

    async def _snapshot(self, page: Any) -> Snapshot:
        items = await page.evaluate(_TAG_JS, REF_ATTR)
        try:
            text = await page.locator("body").aria_snapshot(timeout=ACTION_TIMEOUT_MS)
        except Exception:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        refs: dict[str, dict[str, Any]] = {}
        for item in items:
            ref = item.pop("ref")
            refs[ref] = {**item, "selector": f'[{REF_ATTR}="{ref}"]'}
        return Snapshot(str(page.url), await page.title(), str(text), refs)
