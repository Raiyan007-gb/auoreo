from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part


def scale_note(
    region: tuple[int, ...] | None, orig: tuple[int, int], sent: tuple[int, int]
) -> tuple[str, dict]:
    """How image pixels map back to screen pixels (I8, spec §14.1 region `scale_note` remap)."""
    sx = orig[0] / sent[0] if sent[0] else 1.0
    sy = orig[1] / sent[1] if sent[1] else 1.0
    left, top = (region[0], region[1]) if region else (0, 0)
    meta = {"scale_x": round(sx, 6), "scale_y": round(sy, 6), "offset_x": left, "offset_y": top}
    if sx == 1.0 and sy == 1.0 and not region:
        return "", meta
    note = (
        f"scale_note: image pixel (x, y) is screen pixel ({left} + x*{sx:.4g}, {top} + y*{sy:.4g})"
        if region
        else f"scale_note: image pixel (x, y) is screen pixel (x*{sx:.4g}, y*{sy:.4g})"
    )
    return note, meta


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    capture = ctx.backend("capture")
    region = tuple(args["region"]) if args.get("region") else None
    if region is not None and (region[2] <= 0 or region[3] <= 0):
        raise ToolError("region width and height must be positive")
    png = await capture.grab(monitor=args.get("monitor"), region=region)
    where = f"region {list(region)}" if region else f"monitor {args.get('monitor', 1)}"
    image, meta = await to_part(png, f"screenshot of {where}")
    note, remap = scale_note(
        region, (meta["orig_width"], meta["orig_height"]), (meta["width"], meta["height"])
    )
    text = f"Captured {where}: {meta['width']}x{meta['height']} {image.media_type}, {meta['bytes']} bytes."
    if note:
        text += f"\n{note}"
    return ToolResult(
        text, meta={"images": 1, "width": meta["width"], "height": meta["height"], **remap}, images=[image]
    )
