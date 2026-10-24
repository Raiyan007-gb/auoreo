from auoreo.tools.context import ToolContext, ToolError

MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


async def run(args: dict, ctx: ToolContext) -> str:
    todos = args["todos"]
    if sum(t["status"] == "in_progress" for t in todos) > 1:
        raise ToolError("only one todo may be in_progress at a time")
    ctx.state.todos = todos
    await ctx.emit("todo.updated", {"todos": todos})
    done = sum(t["status"] == "completed" for t in todos)
    lines = [f"{MARK[t['status']]} {t['content']}" for t in todos]
    return f"todos ({done}/{len(todos)} done):\n" + "\n".join(lines)
