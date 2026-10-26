"""Image helpers shared by multimodal tools (screenshot, image_info, vision_understand, …).

`sniff` trusts magic bytes, never file extensions; SVG (scriptable vector markup) and
unknown formats are rejected. `normalize` applies the size ladder before an image is
sent to a model: EXIF orientation applied and metadata stripped, long edge ≤ 1568 px,
JPEG at quality 85 → 70 → 50 (then downscaled) until ≤ 256 KB; PNG is kept only for a
small image with real transparency. `ask_vision` is the one aux vision call.

I8 (spec §14.1): downscaling never takes the short edge below `MIN_SHORT_EDGE` (64 px);
`ask_vision` runs at temperature 0.1 — natively on the session model when it accepts images
(`path: native`), else on the `vision` routing group (`path: aux`, 120 s budget) — fails closed
to aux when a native call errors, and on a provider "image too large" refusal (Anthropic: 5 MB /
8000 px) downscales the image by half and retries once. `fit_for_provider` pre-shrinks anything
over 5 MB / 8000 px before it is sent.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps, UnidentifiedImageError

from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import ImagePart, LLMRequest, TextDelta
from auoreo.tools.context import ToolError

if TYPE_CHECKING:
    from auoreo.tools.context import ToolContext

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_EDGE = 1568
TARGET_BYTES = 256 * 1024
JPEG_LADDER = (85, 70, 50)
MIN_EDGE = 256
MIN_SHORT_EDGE = 64
PROVIDER_MAX_BYTES = 5 * 1024 * 1024
PROVIDER_MAX_PX = 8000
VISION_TEMPERATURE = 0.1
AUX_TIMEOUT_S = 120.0
_TOO_LARGE = ("too large", "exceeds", "5 mb", "5mb", "8000", "dimension", "image size", "maximum allowed")
MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
VISION_GROUP = "vision"


def sniff(data: bytes) -> str:
    """`png` | `jpeg` | `gif` | `webp`; raises `ToolError` for SVG or anything else."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    text = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith(b"<svg") or (text.startswith((b"<?xml", b"<!doctype svg")) and b"<svg" in text):
        raise ToolError(
            "SVG images are not supported (vector markup can carry scripts); rasterize to PNG first"
        )
    raise ToolError("not a supported image (expected PNG, JPEG, GIF or WebP by magic bytes)")


def _open(data: bytes) -> tuple[Image.Image, str]:
    if len(data) > MAX_INPUT_BYTES:
        raise ToolError(f"image is {len(data)} bytes; the limit is {MAX_INPUT_BYTES} bytes (20 MB)")
    fmt = sniff(data)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # first frame for animated GIF/WebP
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ToolError(f"cannot decode {fmt} image: {exc}") from exc
    return img, fmt


def info(data: bytes) -> dict[str, Any]:
    """Format, dimensions and size without re-encoding."""
    img, fmt = _open(data)
    return {
        "format": fmt,
        "media_type": MEDIA_TYPES[fmt],
        "width": img.width,
        "height": img.height,
        "mode": img.mode,
        "bytes": len(data),
        "frames": getattr(img, "n_frames", 1),
    }


def _has_alpha(img: Image.Image) -> bool:
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode not in ("RGBA", "LA", "PA"):
        return False
    lo = img.getchannel("A").getextrema()[0]
    return isinstance(lo, (int, float)) and lo < 255


def _encode(img: Image.Image, fmt: str, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _shrink(img: Image.Image, factor: float) -> Image.Image:
    """Resize by `factor` but never below `MIN_SHORT_EDGE` on the short side (64 px floor)."""
    short = min(img.size)
    if short * factor < MIN_SHORT_EDGE:
        factor = min(1.0, MIN_SHORT_EDGE / max(short, 1))
    if factor >= 1.0:
        return img
    size = (max(1, round(img.width * factor)), max(1, round(img.height * factor)))
    return img.resize(size, Image.Resampling.LANCZOS)


def fit_for_provider(
    data: bytes, max_bytes: int = PROVIDER_MAX_BYTES, max_px: int = PROVIDER_MAX_PX, factor: float = 1.0
) -> tuple[bytes, str]:
    """Shrink (by `factor`, then until ≤ `max_px` per side and ≤ `max_bytes`) → `(jpeg|png, media_type)`."""
    img, fmt = _open(data)
    img = ImageOps.exif_transpose(img)
    if max(img.size) > max_px:
        factor = min(factor, max_px / max(img.size))
    img = _shrink(img, factor) if factor < 1.0 else img
    out = _encode(img.convert("RGB"), "JPEG", JPEG_LADDER[0])
    while len(out) > max_bytes and min(img.size) * 3 // 4 >= MIN_SHORT_EDGE:
        img = _shrink(img, 0.75)
        out = _encode(img.convert("RGB"), "JPEG", JPEG_LADDER[-1])
    return out, "image/jpeg"


def normalize(data: bytes) -> tuple[bytes, str, dict[str, Any]]:
    """Re-encode `data` for a model: returns `(bytes, media_type, info)`."""
    img, fmt = _open(data)
    orig = (img.width, img.height)
    img = ImageOps.exif_transpose(img)  # copy with orientation applied
    img.info.clear()  # drop EXIF/ICC/text chunks: nothing from the source survives
    if max(img.size) > MAX_EDGE:
        img = _shrink(img, MAX_EDGE / max(img.size))
    meta: dict[str, Any] = {"source_format": fmt, "orig_width": orig[0], "orig_height": orig[1]}
    meta["orig_bytes"] = len(data)
    if _has_alpha(img):
        rgba = img.convert("RGBA")
        png = _encode(rgba, "PNG")
        if len(png) <= TARGET_BYTES:
            return png, "image/png", {**meta, "width": rgba.width, "height": rgba.height, "bytes": len(png)}
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    img = img.convert("RGB")
    while True:
        for q in JPEG_LADDER:
            out = _encode(img, "JPEG", q)
            if len(out) <= TARGET_BYTES:
                break
        if len(out) <= TARGET_BYTES or max(img.size) <= MIN_EDGE or min(img.size) * 3 // 4 < MIN_SHORT_EDGE:
            break
        img = _shrink(img, 0.75)
    return (
        out,
        "image/jpeg",
        {**meta, "width": img.width, "height": img.height, "bytes": len(out), "quality": q},
    )


async def to_part(data: bytes, note: str) -> tuple[ImagePart, dict[str, Any]]:
    """Normalize off the event loop and wrap as an `ImagePart` (note gains the size)."""
    out, media_type, meta = await asyncio.to_thread(normalize, data)
    label = f"{note} ({meta['width']}x{meta['height']} {media_type})".strip()
    return ImagePart(media_type, base64.b64encode(out).decode("ascii"), label), meta


def read_capped(p: Path, limit: int) -> bytes:
    """Read a local file, refusing missing files and files over `limit` bytes."""
    if not p.is_file():
        raise ToolError(f"file not found: {p}")
    size = p.stat().st_size
    if size > limit:
        raise ToolError(f"{p} is {size} bytes; the limit is {limit} bytes ({limit // (1024 * 1024)} MB)")
    return p.read_bytes()


async def load_source(ctx: ToolContext, tool: str, path: str | None, url: str | None) -> tuple[bytes, str]:
    """Bytes of an image from a local path (the tool's own `read` permission covers it)
    or an http(s) URL (checked as `webfetch` + SSRF guard). Returns `(bytes, label)`."""
    if bool(path) == bool(url):
        raise ToolError("give exactly one of `path` or `url`")
    if path:
        p = ctx.resolve_path(path)
        return await asyncio.to_thread(read_capped, p, MAX_INPUT_BYTES), str(p)
    from auoreo.tools.net import safe_request

    assert url is not None
    await ctx.authorize(tool, "webfetch", url)
    res = await safe_request("GET", url, headers={"User-Agent": "auoreo/0.1"}, max_bytes=MAX_INPUT_BYTES)
    if res.status >= 400:
        raise ToolError(f"fetching {url} failed with HTTP {res.status}")
    if res.truncated:
        raise ToolError(f"image at {url} exceeds {MAX_INPUT_BYTES} bytes (20 MB)")
    return res.body, res.url


def vision_model(ctx: ToolContext) -> str:
    """Session model when it can see images, else the `vision` routing group."""
    svc = ctx.services.get("services")
    spec = ctx.services.get("spec")
    if svc is None:
        raise ToolError("no services attached; cannot call a vision model")
    model = getattr(spec, "model", None) or svc.settings.model or "default"
    if svc.gateway.supports_vision(model):
        return str(model)
    if VISION_GROUP in svc.settings.models:
        return VISION_GROUP
    raise ToolError(
        f"no vision-capable model: '{model}' cannot read images and settings.models has no "
        f"'{VISION_GROUP}' routing group; add one (e.g. models.vision.models=[…])"
    )


def _too_large(exc: GatewayError) -> bool:
    text = f"{exc.message} {exc.cause or ''}".lower()
    return (exc.status in (400, 413) or exc.code in ("INVALID_REQUEST", "PAYLOAD_TOO_LARGE")) and any(
        k in text for k in _TOO_LARGE
    )


def _halve(image: ImagePart) -> ImagePart:
    data, media_type = fit_for_provider(base64.b64decode(image.data_b64), factor=0.5)
    return ImagePart(media_type, base64.b64encode(data).decode("ascii"), f"{image.note} (downscaled)")


async def _vision_call(ctx: ToolContext, model: str, image: ImagePart, question: str, purpose: str) -> str:
    svc = ctx.services["services"]
    request = LLMRequest(
        model=model,
        messages=(
            {
                "role": "system",
                "content": "You answer questions about the attached image precisely and briefly.",
            },
            {"role": "user", "content": [{"type": "text", "text": question}, image.part()]},
        ),
        tool_choice="none",
        temperature=VISION_TEMPERATURE,
        metadata={"purpose": purpose, "session_id": ctx.session_id},
    )
    parts: list[str] = []

    async def collect() -> None:
        async for item in svc.gateway.stream(request):
            if isinstance(item, TextDelta):
                parts.append(item.text)

    await asyncio.wait_for(collect(), AUX_TIMEOUT_S)
    return "".join(parts).strip()


async def ask_vision(
    ctx: ToolContext, image: ImagePart, question: str, purpose: str = "vision"
) -> tuple[str, str]:
    """ONE vision call with the image attached (+ at most one size retry or aux fallback).
    Returns `(answer, model)`; `ctx.state`-free, the path is reported by `vision_path`."""
    model = vision_model(ctx)
    svc = ctx.services["services"]
    raw = base64.b64decode(image.data_b64)
    if len(raw) > PROVIDER_MAX_BYTES:
        image = _halve(image)
    tries: list[str] = [model]
    if model != VISION_GROUP and VISION_GROUP in svc.settings.models:
        tries.append(VISION_GROUP)  # fail closed to aux
    resized = False
    answer, used, last = "", model, None
    for candidate in tries:
        try:
            answer = await _vision_call(ctx, candidate, image, question, purpose)
            used = candidate
            break
        except GatewayError as exc:
            last = exc
            if _too_large(exc) and not resized:
                resized, image = True, _halve(image)
                try:
                    answer = await _vision_call(ctx, candidate, image, question, purpose)
                    used = candidate
                    break
                except GatewayError as exc2:
                    last = exc2
        except TimeoutError:
            last = GatewayError("TIMEOUT", f"no answer within {AUX_TIMEOUT_S:.0f}s")
    else:
        assert last is not None
        raise ToolError(f"vision call to '{model}' failed ({last.code}): {last.message}") from last
    if not answer:
        raise ToolError(f"vision model '{used}' returned no text")
    return answer, used


def vision_path(model: str) -> str:
    return "aux" if model == VISION_GROUP else "native"
