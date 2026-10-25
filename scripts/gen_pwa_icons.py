"""Render the PWA / desktop icons from the `favicon.svg` geometry with Pillow.

sharp is not a web dependency, so the PNGs are generated here and committed:
    uv run python scripts/gen_pwa_icons.py
favicon.svg: a #18181b disc (r=14/32) with a #fafafa ring (r=8/32, stroke 3/32).
"maskable" variants are full-bleed with the ring inside the 80 % safe zone.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "web" / "app" / "public" / "icons"
DARK, LIGHT = (0x18, 0x18, 0x1B, 255), (0xFA, 0xFA, 0xFA, 255)
SS = 4  # supersampling factor for smooth edges


def render(size: int, *, maskable: bool) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), DARK if maskable else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    if not maskable:
        r = s * 14 / 32
        d.ellipse((c - r, c - r, c + r, c + r), fill=DARK)
    scale = 0.8 if maskable else 1.0
    ring, width = s * 8 / 32 * scale, s * 3 / 32 * scale
    outer, inner = ring + width / 2, ring - width / 2
    d.ellipse((c - outer, c - outer, c + outer, c + outer), fill=LIGHT)
    d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=DARK)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        render(size, maskable=False).save(OUT / f"icon-{size}.png", optimize=True)
        render(size, maskable=True).save(OUT / f"maskable-{size}.png", optimize=True)
    render(180, maskable=True).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
