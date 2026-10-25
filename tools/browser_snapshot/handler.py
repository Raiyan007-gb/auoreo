from auoreo.backends.browser import browser_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    snap = await browser_for(ctx).snapshot(ctx.session_id)
    text = snap.render(int(args.get("max_chars", 20_000)))
    return ToolResult(text, meta={"url": snap.url, "title": snap.title, "refs": len(snap.refs)})
