from auoreo.backends.browser import browser_for
from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    closed = await browser_for(ctx).close_session(ctx.session_id)
    return "browser closed" if closed else "no browser was open for this session"
