from auoreo.backends.browser import browser_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    snap = await browser_for(ctx).type(
        ctx.session_id,
        args["text"],
        ref=args.get("ref"),
        selector=args.get("selector"),
        submit=bool(args.get("submit", False)),
    )
    return ToolResult(snap.render(), meta={"url": snap.url, "title": snap.title, "refs": len(snap.refs)})
