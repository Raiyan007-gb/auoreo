"""Browser backend ABC (M4b): one isolated page per session, driven by `browser_*` tools.

Implementations (`backends/browser/<name>/impl.py`) supply the page primitives
(`_open_page`, `_goto`, `_click`, …). The navigation *policy* lives here so every
implementation gets it: each target URL passes `auoreo.tools.net.check_url` (http/https
only; private, loopback and link-local hosts blocked unless `settings.allow_private_urls`),
and the URL the page actually lands on after a navigation, click, submit or back is
re-checked, so a redirect or link into a private network is refused (the page is reset to
`about:blank`).

Element refs: a snapshot tags every interactive element (links, buttons, inputs,
textareas, selects, checkboxes, …) with a short id `e1`, `e2`, … in document order; the
agent passes that id to click/type/select. Refs stay valid until the next snapshot.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from auoreo.backends import BackendStatus
from auoreo.tools.context import ToolError
from auoreo.tools.net import check_url

USER_AGENT_SUFFIX = "auoreo/0.1"
VIEWPORT = {"width": 1280, "height": 800}
REF_ATTR = "data-auoreo-ref"
WAIT_STATES = ("load", "domcontentloaded", "networkidle")
_BLANK = "about:blank"


def ref_selector(ref: str) -> str:
    return f'[{REF_ATTR}="{ref}"]'


@dataclass
class Snapshot:
    """Token-cheap view of a page: an accessibility outline plus clickable refs."""

    url: str
    title: str
    text: str
    refs: dict[str, dict[str, Any]] = field(default_factory=dict)  # e1 -> {role, name, selector}

    def render(self, max_chars: int = 20_000) -> str:
        lines = [f"Page: {self.title or '(untitled)'}", f"URL: {self.url}", ""]
        body = self.text.strip()
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n… [{len(self.text) - max_chars} chars truncated]"
        lines += [body or "(empty page)", ""]
        if self.refs:
            lines.append("Interactive elements (pass `ref` to browser_click/browser_type/browser_select):")
            for ref, info in self.refs.items():
                name = info.get("name") or ""
                extra = f" = {info['value']!r}" if info.get("value") else ""
                lines.append(f"[{ref}] {info.get('role', 'element')} {name!r}{extra}".rstrip())
        else:
            lines.append("No interactive elements.")
        return "\n".join(lines)


async def check_navigation(url: str, allow_private: bool) -> None:
    """The browser SSRF policy for one URL (raises `ToolError`)."""
    scheme = urlsplit(url).scheme.lower()
    if scheme in {"file", "javascript", "data", "blob", "chrome", "about", "view-source"}:
        raise ToolError(f"'{scheme}:' navigations are blocked; only http/https URLs are allowed")
    await check_url(url, allow_private)


class BrowserBackend(ABC):
    """Base class for `browser` backends. Subclasses implement the page primitives."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._refs: dict[str, dict[str, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def allow_private(self) -> bool:
        return bool(getattr(self.settings, "allow_private_urls", False))

    # ----------------------------------------------------------- primitives
    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def _open_page(self, session_id: str) -> Any:
        """Create the session's isolated context + page."""

    @abstractmethod
    def _get_page(self, session_id: str) -> Any | None:
        """The session's page if one is open."""

    @abstractmethod
    async def _close_page(self, session_id: str) -> None: ...

    @abstractmethod
    async def _close_all(self) -> None: ...

    @abstractmethod
    async def _goto(self, page: Any, url: str, wait: str) -> None: ...

    @abstractmethod
    async def _current_url(self, page: Any) -> str: ...

    @abstractmethod
    async def _snapshot(self, page: Any) -> Snapshot:
        """Tag interactive elements with `REF_ATTR` and return the snapshot."""

    @abstractmethod
    async def _click(self, page: Any, selector: str) -> None: ...

    @abstractmethod
    async def _type(self, page: Any, selector: str, text: str, submit: bool) -> None: ...

    @abstractmethod
    async def _select(self, page: Any, selector: str, value: str) -> list[str]: ...

    @abstractmethod
    async def _screenshot(self, page: Any, full_page: bool) -> bytes: ...

    @abstractmethod
    async def _back(self, page: Any) -> None: ...

    # ------------------------------------------------------------ lifecycle
    def has_page(self, session_id: str) -> bool:
        return self._get_page(session_id) is not None

    async def page(self, session_id: str) -> Any:
        """The session's page, created lazily (one isolated context per session)."""
        existing = self._get_page(session_id)
        if existing is not None:
            return existing
        async with self._lock(session_id):
            existing = self._get_page(session_id)
            return existing if existing is not None else await self._open_page(session_id)

    async def close_session(self, session_id: str) -> bool:
        """Close the session's page and context. Idempotent; True if one was open."""
        self._refs.pop(session_id, None)
        self._locks.pop(session_id, None)
        if self._get_page(session_id) is None:
            return False
        await self._close_page(session_id)
        return True

    async def close(self) -> None:
        self._refs.clear()
        self._locks.clear()
        await self._close_all()

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _require(self, session_id: str) -> Any:
        page = self._get_page(session_id)
        if page is None:
            raise ToolError("no browser page is open for this session; call browser_navigate first")
        return page

    # --------------------------------------------------------------- policy
    async def _enforce_landing(self, page: Any) -> None:
        """Re-check where the page ended up (redirects, link clicks, form posts, history)."""
        landed = await self._current_url(page)
        if not landed or landed == _BLANK:
            return
        try:
            await check_navigation(landed, self.allow_private)
        except ToolError as exc:
            await self._goto(page, _BLANK, "load")
            raise ToolError(f"navigation landed on a blocked URL {landed}: {exc}") from exc

    def _selector(self, session_id: str, ref: str | None, selector: str | None) -> str:
        if ref:
            info = self._refs.get(session_id, {}).get(ref)
            if info is None:
                raise ToolError(f"unknown ref '{ref}'; take a fresh browser_snapshot and use its refs")
            return str(info["selector"])
        if selector:
            return selector
        raise ToolError("pass either `ref` (from a snapshot) or `selector`")

    # -------------------------------------------------------------- actions
    async def navigate(self, session_id: str, url: str, wait: str = "load") -> Snapshot:
        if wait not in WAIT_STATES:
            raise ToolError(f"wait must be one of {', '.join(WAIT_STATES)}")
        await check_navigation(url, self.allow_private)
        page = await self.page(session_id)
        try:
            await self._goto(page, url, wait)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"navigation to {url} failed: {exc}") from exc
        await self._enforce_landing(page)
        return await self.snapshot(session_id)

    async def snapshot(self, session_id: str) -> Snapshot:
        page = self._require(session_id)
        snap = await self._snapshot(page)
        self._refs[session_id] = snap.refs
        return snap

    async def click(self, session_id: str, ref: str | None = None, selector: str | None = None) -> Snapshot:
        page = self._require(session_id)
        await self._guard(self._click(page, self._selector(session_id, ref, selector)), "click")
        await self._enforce_landing(page)
        return await self.snapshot(session_id)

    async def type(
        self,
        session_id: str,
        text: str,
        ref: str | None = None,
        selector: str | None = None,
        submit: bool = False,
    ) -> Snapshot:
        page = self._require(session_id)
        await self._guard(self._type(page, self._selector(session_id, ref, selector), text, submit), "type")
        await self._enforce_landing(page)
        return await self.snapshot(session_id)

    async def select(
        self, session_id: str, value: str, ref: str | None = None, selector: str | None = None
    ) -> Snapshot:
        page = self._require(session_id)
        chosen = await self._guard(
            self._select(page, self._selector(session_id, ref, selector), value), "select"
        )
        if not chosen:
            raise ToolError(f"no option matching {value!r}")
        await self._enforce_landing(page)
        return await self.snapshot(session_id)

    async def screenshot(self, session_id: str, full_page: bool = False) -> bytes:
        page = self._require(session_id)
        data: bytes = await self._guard(self._screenshot(page, full_page), "screenshot")
        return data

    async def back(self, session_id: str) -> Snapshot:
        page = self._require(session_id)
        await self._guard(self._back(page), "back")
        await self._enforce_landing(page)
        return await self.snapshot(session_id)

    @staticmethod
    async def _guard(coro: Any, what: str) -> Any:
        """Turn library errors (timeouts, detached elements) into model-visible errors."""
        try:
            return await coro
        except ToolError:
            raise
        except Exception as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            raise ToolError(f"{what} failed: {first}") from exc


class _ApprovalGate:
    """I8: wraps a backend that drives the user's real browser profile (`approval_permission`,
    e.g. the Chrome CDP bridge): the first page call of a session asks for that permission
    (pattern = the CDP endpoint), then delegates. Process memory only, per backend + session."""

    GATED = frozenset({"navigate", "snapshot", "click", "type", "select", "screenshot", "back"})

    def __init__(self, inner: BrowserBackend, ctx: Any) -> None:
        self._inner, self._ctx = inner, ctx

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in self.GATED:
            return attr

        async def gated(*args: Any, **kwargs: Any) -> Any:
            approved: set[str] = self._inner.__dict__.setdefault("_approved_sessions", set())
            sid = str(self._ctx.session_id or "")
            if sid not in approved:
                perm = str(getattr(self._inner, "approval_permission", "browser"))
                await self._ctx.authorize(
                    "browser", perm, str(getattr(self._inner, "approval_pattern", perm)), "ask"
                )
                approved.add(sid)
            return await attr(*args, **kwargs)

        return gated


def browser_for(ctx: Any) -> BrowserBackend:
    """The selected browser backend from a `ToolContext` (raises `ToolError`). A backend with
    `approval_permission` (real-profile bridges) is returned behind a per-session approval gate."""
    inst = ctx.backend("browser")
    if not isinstance(inst, BrowserBackend):
        raise ToolError(f"browser backend {type(inst).__name__} does not implement BrowserBackend")
    if getattr(inst, "approval_permission", None):
        gate: Any = _ApprovalGate(inst, ctx)
        return gate  # type: ignore[no-any-return]
    return inst
