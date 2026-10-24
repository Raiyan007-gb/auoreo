from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    ctx.state.plan = args["steps"]
    await ctx.emit("plan.updated", {"steps": args["steps"], "explanation": args.get("explanation", "")})
    return "plan updated:\n" + "\n".join(
        f"{i}. [{s['status']}] {s['step']}" for i, s in enumerate(args["steps"], 1)
    )
