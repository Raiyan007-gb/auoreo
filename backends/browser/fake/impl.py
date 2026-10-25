"""In-memory fake browser: canned HTML keyed by URL, deterministic snapshots.

Tests set `backend.pages = {url: html}` (and optionally `backend.redirects = {url: url}`).
Unknown URLs render a small generic page so keyless demos work. Clicks on links follow
`href`; clicks on submit buttons (or `type` with `submit`) send the enclosing form as a
GET query string. Every action is recorded on the `FakePage` for assertions.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

from auoreo.backends import BackendStatus
from auoreo.backends.browser import BrowserBackend, Snapshot, ref_selector
from auoreo.tools.context import ToolError

_INTERACTIVE = {"a", "button", "input", "textarea", "select"}
_SKIP_TEXT = {"script", "style", "title", "option", "textarea"}
_BUTTON_TYPES = {"submit", "button", "reset", "image"}


@dataclass
class _El:
    tag: str
    attrs: dict[str, str]
    form: int | None
    text: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, label)


class _Doc(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lines: list[str] = []
        self.els: list[_El] = []
        self.forms: list[dict[str, str]] = []
        self._stack: list[str] = []
        self._open: _El | None = None
        self._form: int | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: v or "" for k, v in attrs_list}
        if tag == "form":
            self.forms.append(attrs)
            self._form = len(self.forms) - 1
        elif tag == "option" and self.els and self.els[-1].tag == "select":
            self.els[-1].options.append((attrs.get("value", ""), ""))
        elif tag in _INTERACTIVE and not (tag == "input" and attrs.get("type") == "hidden"):
            el = _El(tag, attrs, self._form)
            self.els.append(el)
            if tag in {"a", "button", "textarea"}:
                self._open = el
        if tag not in {"input", "br", "img", "meta", "link", "hr"}:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None
        if self._open is not None and tag == self._open.tag:
            self._open = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        top = self._stack[-1] if self._stack else ""
        if top == "title":
            self.title += text
        elif top == "option" and self.els and self.els[-1].options:
            value, _ = self.els[-1].options[-1]
            self.els[-1].options[-1] = (value or text, text)
        if self._open is not None:
            self._open.text = f"{self._open.text} {text}".strip()
        if top not in _SKIP_TEXT:
            self.lines.append(text)


def _role(el: _El) -> str:
    if el.attrs.get("role"):
        return el.attrs["role"]
    if el.tag == "input":
        kind = el.attrs.get("type", "text")
        return {"checkbox": "checkbox", "radio": "radio"}.get(
            kind, "button" if kind in _BUTTON_TYPES else "textbox"
        )
    return {"a": "link", "button": "button", "select": "combobox", "textarea": "textbox"}[el.tag]


def _name(el: _El) -> str:
    a = el.attrs
    return (a.get("aria-label") or el.text or a.get("placeholder") or a.get("name") or a.get("value") or "")[
        :100
    ]


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


@dataclass
class FakePage:
    url: str = "about:blank"
    history: list[str] = field(default_factory=list)
    values: dict[int, str] = field(default_factory=dict)  # element index -> typed/selected value
    checked: set[int] = field(default_factory=set)
    clicks: list[str] = field(default_factory=list)
    typed: list[tuple[str, str]] = field(default_factory=list)
    doc: _Doc = field(default_factory=lambda: _Doc(""))


class Backend(BrowserBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.pages: dict[str, str] = {}
        self.redirects: dict[str, str] = {}
        self.navigations: list[str] = []
        self.sessions: dict[str, FakePage] = {}

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "in-memory fake browser (canned pages)")

    async def _open_page(self, session_id: str) -> FakePage:
        page = self.sessions[session_id] = FakePage()
        return page

    def _get_page(self, session_id: str) -> FakePage | None:
        return self.sessions.get(session_id)

    async def _close_page(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    async def _close_all(self) -> None:
        self.sessions.clear()

    # ------------------------------------------------------------ navigation
    def _html_for(self, url: str) -> str:
        if url in self.pages:
            return self.pages[url]
        if url == "about:blank":
            return ""
        host = escape(urlsplit(url).hostname or url)
        return (
            f"<html><head><title>{host}</title></head><body><h1>Fake page for {escape(url)}</h1>"
            '<p>This page is served by the fake browser backend.</p><a href="/">Home</a></body></html>'
        )

    def _load(self, page: FakePage, url: str, *, record: bool = True) -> None:
        for _ in range(10):
            if url not in self.redirects:
                break
            url = urljoin(url, self.redirects[url])
        if record and page.url != "about:blank":
            page.history.append(page.url)
        self.navigations.append(url)
        page.url, page.doc = url, _Doc(self._html_for(url))
        page.values, page.checked = {}, set()

    async def _goto(self, page: FakePage, url: str, wait: str) -> None:
        self._load(page, url)

    async def _current_url(self, page: FakePage) -> str:
        return page.url

    async def _back(self, page: FakePage) -> None:
        if not page.history:
            raise ToolError("no previous page in this session's history")
        self._load(page, page.history.pop(), record=False)

    # -------------------------------------------------------------- elements
    def _index(self, page: FakePage, selector: str) -> int:
        m = re.fullmatch(r'\[data-auoreo-ref="e(\d+)"\]', selector)
        if m and 0 < int(m.group(1)) <= len(page.doc.els):
            return int(m.group(1)) - 1
        key, want = ("id", selector[1:]) if selector.startswith("#") else (None, None)
        nm = re.fullmatch(r"""\[name=["']?([^"'\]]+)["']?\]""", selector)
        if nm:
            key, want = "name", nm.group(1)
        for i, el in enumerate(page.doc.els):
            if key and el.attrs.get(key) == want:
                return i
        raise ToolError(f"no element matches selector {selector!r}")

    def _submit(self, page: FakePage, form: int | None) -> None:
        if form is None:
            return
        spec = page.doc.forms[form]
        fields: list[tuple[str, str]] = []
        for i, el in enumerate(page.doc.els):
            name = el.attrs.get("name")
            if el.form != form or not name or _role(el) == "button":
                continue
            if _role(el) == "checkbox" and i not in page.checked:
                continue
            default = (
                el.options[0][0]
                if el.options
                else el.attrs.get("value", el.text if el.tag == "textarea" else "")
            )
            fields.append((name, page.values.get(i, default)))
        action = urljoin(page.url, spec.get("action") or page.url)
        self._load(page, f"{action.split('?')[0]}?{urlencode(fields)}")

    async def _click(self, page: FakePage, selector: str) -> None:
        i = self._index(page, selector)
        el = page.doc.els[i]
        page.clicks.append(selector)
        role = _role(el)
        if el.tag == "a" and el.attrs.get("href"):
            self._load(page, urljoin(page.url, el.attrs["href"]))
        elif role in {"checkbox", "radio"}:
            page.checked ^= {i}
        elif role == "button" and el.attrs.get("type", "submit") == "submit":
            self._submit(page, el.form)

    async def _type(self, page: FakePage, selector: str, text: str, submit: bool) -> None:
        i = self._index(page, selector)
        if _role(page.doc.els[i]) != "textbox":
            raise ToolError(f"element {selector!r} is not a text field")
        page.values[i] = text
        page.typed.append((selector, text))
        if submit:
            self._submit(page, page.doc.els[i].form)

    async def _select(self, page: FakePage, selector: str, value: str) -> list[str]:
        i = self._index(page, selector)
        for opt_value, label in page.doc.els[i].options:
            if value in {opt_value, label}:
                page.values[i] = opt_value
                return [opt_value]
        return []

    async def _screenshot(self, page: FakePage, full_page: bool) -> bytes:
        return _png()

    async def _snapshot(self, page: FakePage) -> Snapshot:
        refs: dict[str, dict[str, Any]] = {}
        for i, el in enumerate(page.doc.els):
            ref = f"e{i + 1}"
            info: dict[str, Any] = {"role": _role(el), "name": _name(el), "selector": ref_selector(ref)}
            if i in page.values:
                info["value"] = page.values[i]
            if i in page.checked:
                info["checked"] = True
            refs[ref] = info
        return Snapshot(page.url, page.doc.title, "\n".join(page.doc.lines), refs)
