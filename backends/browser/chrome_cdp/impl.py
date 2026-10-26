"""Chrome CDP bridge (spec §14.2 "Chrome CDP bridge for real-profile flows"; I8).

Drives a Chrome YOU started with remote debugging, e.g.

    google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.auoreo/chrome-profile"

`playwright.chromium.connect_over_cdp(i8.browser.cdp_url)` attaches to it; each AUOREO session
gets a new tab in the browser's DEFAULT context, i.e. with that profile's cookies and logins.
Because of that:

- **approval-gated**: `approval_permission = "browser_cdp"` makes `browser_for()` ask once per
  session (pattern = the CDP URL) before any page call; a `browser_cdp` rule can allow/deny it;
- **loopback only**: a non-loopback `cdp_url` is refused (the DevTools protocol is unauthenticated);
- **honest check**: `GET <cdp_url>/json/version` must answer within 2 s;
- closing a session closes only its tab, never the user's browser or other tabs;
- the navigation SSRF policy and per-request routing of the playwright backend still apply.
"""

from __future__ import annotations

import contextlib
import importlib.util
import ipaddress
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from auoreo.backends import BackendStatus
from auoreo.core.settings_i8 import i8_of
from auoreo.tools.context import ToolError

REMEDY = (
    "start Chrome with --remote-debugging-port=9222 --user-data-dir=<a dedicated profile> and set "
    '"i8": {"browser": {"cdp_url": "http://127.0.0.1:9222"}}'
)


def _playwright_impl() -> Any:
    name = "auoreo_backend_browser_playwright_impl"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent.parent / "playwright" / "impl.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_Base: Any = _playwright_impl().Backend


class Backend(_Base):  # type: ignore[misc,valid-type]
    approval_permission = "browser_cdp"

    @property
    def cdp_url(self) -> str:
        return str(i8_of(self.settings).browser.cdp_url).rstrip("/")

    @property
    def approval_pattern(self) -> str:
        return self.cdp_url

    async def check(self) -> BackendStatus:
        url = self.cdp_url
        if not is_loopback(url):
            return BackendStatus(
                False, f"cdp_url {url} is not loopback (DevTools is unauthenticated)", REMEDY
            )
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url}/json/version")
            info = r.json() if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return BackendStatus(
                False, f"no Chrome DevTools endpoint at {url} ({type(exc).__name__})", REMEDY
            )
        if not info.get("webSocketDebuggerUrl"):
            return BackendStatus(False, f"{url} did not answer /json/version", REMEDY)
        return BackendStatus(
            True, f"{info.get('Browser', 'Chrome')} via CDP {url} (real profile; approval-gated)"
        )

    async def _ensure_browser(self) -> Any:
        async with self._start_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if not is_loopback(self.cdp_url):
                raise ToolError(f"refusing non-loopback cdp_url {self.cdp_url}")
            from playwright.async_api import async_playwright

            if self._pw is None:
                self._pw = await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
            except Exception as exc:
                raise ToolError(
                    f"cannot attach to Chrome at {self.cdp_url} ({type(exc).__name__}); {REMEDY}"
                ) from exc
            return self._browser

    async def _open_page(self, session_id: str) -> Any:
        browser = await self._ensure_browser()
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        if not self.allow_private:
            with contextlib.suppress(Exception):  # an existing context may already route
                await context.route("**/*", self._route)
        page = await context.new_page()
        self._pages[session_id] = (context, page)
        return page

    async def _close_page(self, session_id: str) -> None:
        entry = self._pages.pop(session_id, None)
        if entry is not None:
            with contextlib.suppress(Exception):
                await entry[1].close()  # only our tab; the user's context stays

    async def _close_all(self) -> None:
        for sid in list(self._pages):
            await self._close_page(sid)
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()  # disconnects; never closes the user's Chrome
        self._browser = self._pw = None
