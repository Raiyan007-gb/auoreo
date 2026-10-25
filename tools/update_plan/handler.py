from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    ctx.state.plan = args["steps"]
    await ctx.emit("plan.updated", {"steps": args["steps"], "explanation": args.get("explanation", "")})
    lines = []
    for i, s in enumerate(args["steps"], 1):
        done = f" (done when: {s['done_when']})" if s.get("done_when") else ""
        lines.append(f"{i}. [{s['status']}] {s['step']}{done}")
    return "plan updated:\n" + "\n".join(lines)
