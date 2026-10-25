from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    bridge = getattr(ctx.services.get("services"), "mcp", None)
    if bridge is None:
        raise ToolError("MCP is not enabled in this process")
    servers = [args["server"]] if args.get("server") else bridge.connected()
    if not servers:
        return ToolResult("no MCP servers are connected")
    lines: list[str] = []
    found: dict[str, list[str]] = {}
    for server in servers:
        try:
            resources = await bridge.list_resources(server)
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from exc
        found[server] = [r.uri for r in resources]
        lines.append(f"## {server} ({len(resources)} resources)")
        for r in resources:
            extra = ", ".join(x for x in (r.name, r.mime_type) if x)
            desc = f" — {r.description}" if r.description else ""
            lines.append(f"- {r.uri}" + (f" ({extra})" if extra else "") + desc)
    return ToolResult("\n".join(lines), meta={"resources": found})
