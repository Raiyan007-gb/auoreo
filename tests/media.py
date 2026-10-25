"""Media fixtures generated at test time (no binary files in the repo)."""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image

SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def png(w: int = 64, h: int = 48, color: tuple[int, ...] = (200, 30, 30), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def noise_png(w: int, h: int, seed: int = 7) -> bytes:
    rnd = random.Random(seed)
    img = Image.frombytes("RGB", (w, h), bytes(rnd.getrandbits(8) for _ in range(w * h * 3)))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def gradient_jpeg(w: int, h: int) -> bytes:
    img = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def exif_jpeg(w: int = 200, h: int = 100, orientation: int = 6) -> bytes:
    """Landscape pixels tagged 'rotate 90° CW' plus a camera Make tag that must be stripped."""
    img = Image.new("RGB", (w, h), (10, 120, 10))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "SpyCam"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _assemble(objs: list[bytes]) -> bytes:
    """Number the objects 1..n and write a correct xref table + trailer."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def text_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF written by hand: one Helvetica `BT … Tj ET` text object per page."""
    n = len(pages)
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(n))
        + f"] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET".encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    return _assemble(objs)


def table_pdf() -> bytes:
    """A one-page PDF with a ruled 2x3 table (lines + text) that pdfplumber can extract."""
    cells = [["Name", "Qty", "Price"], ["Apple", "3", "1.20"]]
    ops = ["0.5 w"]
    x0, y0, cw, rh = 72, 700, 100, 24
    for r in range(len(cells) + 1):
        ops.append(f"{x0} {y0 - r * rh} m {x0 + cw * 3} {y0 - r * rh} l S")
    for c in range(4):
        ops.append(f"{x0 + c * cw} {y0} m {x0 + c * cw} {y0 - rh * len(cells)} l S")
    for r, row in enumerate(cells):
        for c, val in enumerate(row):
            ops.append(f"BT /F1 12 Tf {x0 + c * cw + 6} {y0 - (r + 1) * rh + 8} Td ({val}) Tj ET")
    stream = "\n".join(ops).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    return _assemble(objs)


def image_pdf(path: Path) -> Path:
    """An image-only (scanned-looking) PDF: no text layer at all."""
    Image.new("RGB", (400, 300), (240, 240, 240)).save(path, "PDF", resolution=100)
    return path
