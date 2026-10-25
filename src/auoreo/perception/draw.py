"""Draw detection boxes onto an image (Pillow) for the `detect_objects`/`detect_faces` tools."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from PIL import ImageDraw

from auoreo.backends.detection import Box
from auoreo.perception.blobs import decode

PALETTE = ((255, 64, 64), (64, 200, 64), (64, 128, 255), (255, 200, 0), (200, 64, 255), (0, 200, 200))


def annotate(image: bytes, boxes: Sequence[Box]) -> bytes:
    """PNG bytes of `image` with every box outlined and labelled `label score`."""
    img = decode(image)
    draw = ImageDraw.Draw(img)
    width = max(2, round(max(img.size) / 300))
    labels = sorted({b.label for b in boxes})
    for b in boxes:
        color = PALETTE[labels.index(b.label) % len(PALETTE)]
        draw.rectangle((b.x0, b.y0, b.x1, b.y1), outline=color, width=width)
        text = f"{b.label} {b.score:.2f}"
        ty = b.y0 - 12 if b.y0 >= 12 else b.y0 + 1
        tw = draw.textlength(text)
        draw.rectangle((b.x0, ty, b.x0 + tw + 4, ty + 11), fill=color)
        draw.text((b.x0 + 2, ty), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def box_dict(b: Box) -> dict[str, Any]:
    d = asdict(b)
    for k in ("score", "x0", "y0", "x1", "y1"):
        d[k] = round(float(d[k]), 3 if k == "score" else 1)
    return d


def render_boxes(boxes: Sequence[Box]) -> str:
    """One line per box: `- person 0.97 [x0, y0, x1, y1]`."""
    return "\n".join(
        f"- {b.label} {b.score:.2f} [{b.x0:.0f}, {b.y0:.0f}, {b.x1:.0f}, {b.y1:.0f}]" for b in boxes
    )
