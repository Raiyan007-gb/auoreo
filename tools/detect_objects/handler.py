"""Object detection: boxes as text + an annotated image (boxes drawn with Pillow)."""

import asyncio

from auoreo.perception.draw import annotate, box_dict, render_boxes
from auoreo.perception.inputs import load_image
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.images import to_part


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    data, label = await load_image(ctx, "detect_objects", args)
    det = ctx.backend("detection")
    classes = [str(c) for c in args.get("classes") or [] if str(c).strip()]
    threshold = float(args.get("threshold", 0.3))
    boxes = await det.detect(data, classes=classes, threshold=threshold)
    name = getattr(det, "name", "detection")
    vocab = "open-vocabulary" if det.open_vocabulary else "fixed labels"
    head = f"[detection: {name}, {vocab}, threshold {threshold:g}] {len(boxes)} box(es) in {label}"
    text = head + (":\n" + render_boxes(boxes) if boxes else ".")
    images = []
    if args.get("annotate", True):
        png = await asyncio.to_thread(annotate, data, boxes)
        part, _meta = await to_part(png, f"{label} with {len(boxes)} detection box(es)")
        images.append(part)
    meta = {"backend": name, "count": len(boxes), "boxes": [box_dict(b) for b in boxes]}
    return ToolResult(text, meta=meta, images=images)
