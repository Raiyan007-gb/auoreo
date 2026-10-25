from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.voice.service import translate_text


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; translation needs the model gateway")
    out = await translate_text(svc, args["text"], args["target"], args.get("source"))
    return ToolResult(out, meta={"target": args["target"], "source": args.get("source"), "chars": len(out)})
