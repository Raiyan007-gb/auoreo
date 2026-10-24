from markdownify import markdownify

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.net import safe_request


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    res = await safe_request("GET", args["url"], headers={"User-Agent": "auoreo/0.1"})
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
