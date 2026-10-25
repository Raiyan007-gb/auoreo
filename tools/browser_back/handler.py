from auoreo.backends.browser import browser_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    snap = await browser_for(ctx).back(ctx.session_id)
    return ToolResult(snap.render(), meta={"url": snap.url, "title": snap.title, "refs": len(snap.refs)})
