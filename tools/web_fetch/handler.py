"""Fetch a public URL through the egress proxy (`net: proxy`): the proxy resolves the host once,
validates it against the SSRF denylist and pins the connection; redirects are re-validated
hop by hop; the body is capped (5 MiB); HTML is converted to markdown.

I9 (spec §25.15 L1097):
- persistent denial cache: a host that answered 401/403/429 is skipped for
  `i9.fetch.denial_ttl_s` (1 h) instead of being hammered again;
- robots.txt is respected for the `auoreo` user agent (`i9.fetch.respect_robots`); blocked = blocked;
- JS-only / anti-bot pages: the configured readers (Jina Reader, Firecrawl scrape; only when
  their key is set, never for URLs carrying credentials or token-like query parameters) are
  tried; otherwise the result ends with a render-escalation hint for the browser tools.
"""

import re
from urllib.parse import parse_qsl, urlsplit
from urllib.robotparser import RobotFileParser

from markdownify import markdownify

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.net import egress_client, safe_request
from auoreo.tools.search_cache import SearchCache

FETCH_MAX = 5 * 1024 * 1024  # spec §2.8: 5 MiB fetch cap (settings.tools.fetch_max_bytes)
UA = {"User-Agent": "auoreo/0.1"}
ROBOTS_MAX = 512 * 1024
DENY_STATUSES = frozenset({401, 403, 429})
_SECRET_QUERY = re.compile(r"(token|key|secret|sig|signature|password|passwd|auth|session)", re.I)
_JS_HINTS = re.compile(r"enable javascript|requires javascript|javascript is (disabled|required)", re.I)
RENDER_HINT = (
    "[render: this page appears to need JavaScript (little static text). Escalate: "
    "browser_navigate to the URL, then browser_snapshot to read the rendered page.]"
)


def reader_safe(url: str) -> bool:
    """Never hand a third-party reader a URL that carries credentials or token-like params."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        return False
    return not any(_SECRET_QUERY.search(k) for k, _ in parse_qsl(parts.query, keep_blank_values=True))


def looks_js_only(html: str, text: str, min_chars: int) -> bool:
    if "<script" not in html.lower():
        return False
    visible = re.sub(r"\s+", " ", re.sub(r"[#*_>`\[\]()-]", " ", text)).strip()
    return len(visible) < min_chars or bool(_JS_HINTS.search(html[:20_000]))


async def robots_allowed(url: str, cache: SearchCache) -> bool:
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"
    body = await cache.robots(host)
    if body is None:
        try:
            res = await safe_request(
                "GET", f"{host}/robots.txt", headers=UA, max_bytes=ROBOTS_MAX, timeout_s=10
            )
            body = res.body.decode("utf-8", "replace") if 200 <= res.status < 300 else ""
        except ToolError:
            return True  # unreachable robots.txt: the page fetch itself reports the real error
        await cache.put_robots(host, body)
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return parser.can_fetch("auoreo", url)


async def via_reader(svc, settings, url: str) -> tuple[str, str] | None:
    """`(reader name, markdown)` from the first configured reader that returns content."""
    secrets = getattr(svc, "secrets", None)
    available = svc.harness.backends_for("search") if getattr(svc, "harness", None) else {}

    def secret(name: str) -> str | None:
        return secrets.get(name) if secrets is not None else None

    async with egress_client(timeout_s=30) as client:
        for name in settings.i9.fetch.reader_fallback:
            bdef = available.get(name)
            if bdef is None or not (backend := bdef.load()(settings)).configured(secret):
                continue
            try:
                content = await backend.fetch(client, url, secret)
            except Exception:  # a reader failure falls through to the next one / the hint
                continue
            if content:
                return name, content
    return None


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    settings = getattr(svc, "settings", None)
    cap = int(getattr(getattr(settings, "tools", None), "fetch_max_bytes", FETCH_MAX))
    url = args["url"]
    limit = int(args.get("max_chars", 50_000))
    fetch_cfg = settings.i9.fetch if settings is not None else None
    cache = SearchCache(settings.home) if settings is not None else None
    host = (urlsplit(url).hostname or "").lower()
    if cache is not None and fetch_cfg is not None:
        if (status := await cache.denied(host)) is not None:
            return ToolResult(
                f"[skipped] {host} answered HTTP {status} recently; not retried until the denial "
                "expires (i9.fetch.denial_ttl_s). Try web_search or another source.",
                is_error=True,
                meta={"denied": status, "url": url},
            )
        if fetch_cfg.respect_robots and not await robots_allowed(url, cache):
            return ToolResult(
                f"[blocked] robots.txt on {host} disallows {url} for the auoreo user agent "
                "(i9.fetch.respect_robots). Do not retry it.",
                is_error=True,
                meta={"robots": "disallowed", "url": url},
            )
    res = await safe_request("GET", url, headers=UA, max_bytes=cap)
    if cache is not None and fetch_cfg is not None and res.status in DENY_STATUSES:
        await cache.deny(host, res.status, fetch_cfg.denial_ttl_s)
    raw = res.body.decode("utf-8", errors="replace")
    html = "html" in res.content_type
    text = markdownify(raw, heading_style="ATX", strip=["script", "style"]) if html else raw
    meta: dict = {"status": res.status, "url": res.url}
    js_only = html and fetch_cfg is not None and looks_js_only(raw, text, fetch_cfg.js_min_text_chars)
    blocked = res.status in {403, 429, 503}
    note = ""
    can_read = (js_only or blocked) and settings is not None and svc is not None and reader_safe(url)
    if can_read and (got := await via_reader(svc, settings, res.url)) is not None:
        meta["reader"], text = got
        js_only = blocked = False
    if js_only:
        note, meta["render_hint"] = f"\n\n{RENDER_HINT}", "browser"
    truncated = res.truncated or len(text) > limit
    text = text[:limit]
    header = f"[{res.status}] {res.url} ({res.content_type or 'unknown type'})"
    if meta.get("reader"):
        header += f" [via {meta['reader']} reader]"
    if truncated:
        header += " [truncated]"
    meta["truncated"] = truncated
    is_error = res.status >= 400 and not meta.get("reader")
    return ToolResult(f"{header}\n\n{text}{note}", is_error=is_error, meta=meta)
