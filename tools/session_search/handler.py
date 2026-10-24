from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    mem = ctx.services.get("memory")
    if mem is None:
        raise ToolError("memory store is disabled")
    hits = await mem.search_sessions(args["query"], int(args.get("limit", 8)), args.get("session_id"))
    if not hits:
        return "no matching messages"
    return "\n".join(f"[{h.citation}] {h.text.strip()[:400]}" for h in hits)
