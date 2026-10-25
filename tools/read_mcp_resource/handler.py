from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    bridge = getattr(ctx.services.get("services"), "mcp", None)
    if bridge is None:
        raise ToolError("MCP is not enabled in this process")
    try:
        contents = await bridge.read_resource(args["server"], args["uri"])
    except KeyError as exc:
        raise ToolError(str(exc.args[0])) from exc
    parts: list[str] = []
    blobs = 0
    for c in contents:
        text = getattr(c, "text", None)
        if text is not None:
            parts.append(text)
            continue
        blobs += 1
        size = len(getattr(c, "blob", "") or "") * 3 // 4  # base64 → bytes (approx.)
        parts.append(f"[binary resource {c.uri}: ~{size} bytes, {c.mime_type or 'unknown media type'}]")
    if not parts:
        return ToolResult(f"resource {args['uri']} is empty")
    return ToolResult("\n\n".join(parts), meta={"uri": args["uri"], "items": len(contents), "blobs": blobs})
