import json

from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    answer = await ctx.question(args["questions"])
    if answer.get("timed_out"):
        return ToolResult(
            "The user did not answer in time. Proceed with a sensible default and say which one you chose.",
            is_error=True,
        )
    return ToolResult(json.dumps(answer.get("responses", []), ensure_ascii=False))
