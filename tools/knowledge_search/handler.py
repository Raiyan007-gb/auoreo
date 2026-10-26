from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    mem = ctx.services.get("memory")
    if mem is None:
        raise ToolError("memory store is disabled")
    tenant = args.get("tenant")
    if tenant:  # cross-tenant reads are refused + audited (tenant-deny)
        try:
            await mem.authorize_tenant(tenant, session_id=ctx.session_id, tool="knowledge_search")
        except PermissionError as exc:
            raise ToolError(str(exc)) from None
        mem = mem.for_tenant(tenant)
    hits = await mem.search(
        args["query"],
        limit=int(args.get("limit", 8)),
        include_archived=bool(args.get("include_archived")),
        allow_aux=ctx.interactive,  # no HyDE aux call from unattended (job/webhook) sessions
        session_id=ctx.session_id,
    )
    if not hits:
        return "no results"
    budget = int(args.get("max_tokens", 800)) * 4
    out, used = [], 0
    for h in hits:
        line = f"[{h.citation}] {h.text.strip()}"
        if used + len(line) > budget:
            line = line[: max(0, budget - used)]
            if line:
                out.append(line + " …")
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)
