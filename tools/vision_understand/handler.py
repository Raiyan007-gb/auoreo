from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.images import ask_vision, load_source, to_part, vision_path


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    data, label = await load_source(ctx, "vision_understand", args.get("path"), args.get("url"))
    image, meta = await to_part(data, label)
    answer, model = await ask_vision(ctx, image, args["question"])
    return ToolResult(
        answer,
        meta={"model": model, "path": vision_path(model), "width": meta["width"], "height": meta["height"]},
    )
