from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    capture = ctx.backend("capture")
    region = tuple(args["region"]) if args.get("region") else None
    if region is not None and (region[2] <= 0 or region[3] <= 0):
        raise ToolError("region width and height must be positive")
    png = await capture.grab(monitor=args.get("monitor"), region=region)
    where = f"region {list(region)}" if region else f"monitor {args.get('monitor', 1)}"
    image, meta = await to_part(png, f"screenshot of {where}")
    text = f"Captured {where}: {meta['width']}x{meta['height']} {image.media_type}, {meta['bytes']} bytes."
    return ToolResult(
        text, meta={"images": 1, "width": meta["width"], "height": meta["height"]}, images=[image]
    )
