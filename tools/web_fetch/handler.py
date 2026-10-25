"""Fetch a public URL: resolve once, validate against the SSRF denylist, connect to the
pinned IP (`auoreo.tools.net`), cap the body (5 MiB), HTML -> markdown."""

from markdownify import markdownify

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.net import safe_request

FETCH_MAX = 5 * 1024 * 1024  # spec §2.8: 5 MiB fetch cap (settings.tools.fetch_max_bytes)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    cap = int(getattr(getattr(getattr(svc, "settings", None), "tools", None), "fetch_max_bytes", FETCH_MAX))
    res = await safe_request("GET", args["url"], headers={"User-Agent": "auoreo/0.1"}, max_bytes=cap)
    text = res.body.decode("utf-8", errors="replace")
    if "html" in res.content_type:
        text = markdownify(text, heading_style="ATX", strip=["script", "style"])
    limit = int(args.get("max_chars", 50_000))
    truncated = res.truncated or len(text) > limit
    text = text[:limit]
    header = f"[{res.status}] {res.url} ({res.content_type or 'unknown type'})"
    if truncated:
        header += " [truncated]"
    return ToolResult(
        f"{header}\n\n{text}",
        is_error=res.status >= 400,
        meta={"status": res.status, "url": res.url, "truncated": truncated},
    )
