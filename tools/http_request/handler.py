from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.net import safe_request

BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    headers = {k: v for k, v in (args.get("headers") or {}).items() if k.lower() not in BLOCKED_HEADERS}
    body = args.get("body")
    if body is not None and len(body) > 1_000_000:
        raise ToolError("request body over 1 MB")
    res = await safe_request(
        args.get("method", "GET"),
        args["url"],
        headers=headers,
        content=body.encode() if body is not None else None,
        max_bytes=2_000_000,
    )
    text = res.body.decode("utf-8", errors="replace")[: int(args.get("max_chars", 20_000))]
    head = f"HTTP {res.status} {res.url}\ncontent-type: {res.content_type}"
    return ToolResult(f"{head}\n\n{text}", is_error=res.status >= 400, meta={"status": res.status})
