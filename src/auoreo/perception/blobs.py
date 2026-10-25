"""Deterministic colour-blob detector (Pillow only) used by the `fake` detection/face backends.

The image is downscaled to at most `GRID` px on its long edge, the background colour is the
most common (quantised) border colour, and every 4-connected region of pixels that differ
from it by more than `DIST` becomes a blob. `score = fill × contrast` (a solid, strongly
contrasting rectangle scores 1.0; a disc ≈ 0.78), so thresholds behave like a real detector.
"""

from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass

from PIL import Image

GRID = 128
DIST = 48.0
MIN_CELLS = 6


@dataclass(frozen=True)
class Blob:
    x0: float
    y0: float
    x1: float
    y1: float
    score: float
    color: tuple[int, int, int]

    @property
    def aspect(self) -> float:
        """height / width."""
        return (self.y1 - self.y0) / max(1e-6, self.x1 - self.x0)


def decode(image: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image))
    img.load()
    return img.convert("RGB")


def _background(px: list[tuple[int, int, int]], w: int, h: int) -> tuple[int, int, int]:
    border = [px[x] for x in range(w)] + [px[(h - 1) * w + x] for x in range(w)]
    border += [px[y * w] for y in range(h)] + [px[y * w + w - 1] for y in range(h)]
    q = Counter((r >> 4, g >> 4, b >> 4) for r, g, b in border).most_common(1)[0][0]
    members = [p for p in border if (p[0] >> 4, p[1] >> 4, p[2] >> 4) == q]
    n = len(members)
    return (sum(p[0] for p in members) // n, sum(p[1] for p in members) // n, sum(p[2] for p in members) // n)


def _dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def find_blobs(img: Image.Image) -> list[Blob]:
    """Blobs in pixel coordinates of `img`, largest first (ties broken by position)."""
    scale = min(1.0, GRID / max(img.size))
    small = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.NEAREST
    )
    w, h = small.size
    raw = small.tobytes()
    px: list[tuple[int, int, int]] = list(zip(raw[0::3], raw[1::3], raw[2::3], strict=True))
    bg = _background(px, w, h)
    fg = [_dist(p, bg) > DIST for p in px]
    seen = [False] * (w * h)
    sx, sy = img.width / w, img.height / h
    out: list[Blob] = []
    for start in range(w * h):
        if not fg[start] or seen[start]:
            continue
        stack, cells = [start], []
        seen[start] = True
        while stack:
            i = stack.pop()
            cells.append(i)
            x, y = i % w, i // w
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                j = ny * w + nx
                if 0 <= nx < w and 0 <= ny < h and fg[j] and not seen[j]:
                    seen[j] = True
                    stack.append(j)
        if len(cells) < MIN_CELLS:
            continue
        xs, ys = [i % w for i in cells], [i // w for i in cells]
        bx0, bx1, by0, by1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        fill = len(cells) / ((bx1 - bx0) * (by1 - by0))
        mean = tuple(sum(px[i][c] for i in cells) // len(cells) for c in range(3))
        color = (mean[0], mean[1], mean[2])
        contrast = min(1.0, 0.25 + _dist(color, bg) / 255)
        out.append(Blob(bx0 * sx, by0 * sy, bx1 * sx, by1 * sy, round(min(1.0, fill * contrast), 3), color))
    out.sort(key=lambda b: (-(b.x1 - b.x0) * (b.y1 - b.y0), b.y0, b.x0))
    return out
