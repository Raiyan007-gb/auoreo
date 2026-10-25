import asyncio

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import MAX_INPUT_BYTES, info


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"file not found: {args['path']}")
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ToolError(f"{path} is {size} bytes; the limit is {MAX_INPUT_BYTES} bytes (20 MB)")
    data = await asyncio.to_thread(path.read_bytes)
    meta = await asyncio.to_thread(info, data)
    frames = f", {meta['frames']} frames" if meta["frames"] > 1 else ""
    text = (
        f"{path}: {meta['format'].upper()} ({meta['media_type']}), {meta['width']}x{meta['height']} px, "
        f"mode {meta['mode']}{frames}, {meta['bytes']} bytes"
    )
    return ToolResult(text, meta=meta)
