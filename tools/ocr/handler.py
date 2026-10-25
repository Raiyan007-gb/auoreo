import asyncio
import io
import shutil

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.images import ask_vision, load_source, sniff, to_part

TRANSCRIBE = "Transcribe all text in this image verbatim."


def _tesseract(data: bytes) -> tuple[str, float] | None:
    """(text, mean word confidence) or None when Tesseract is unavailable."""
    try:
        import pytesseract  # optional: not a dependency
    except ImportError:
        return None
    if shutil.which("tesseract") is None:
        return None
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(d["conf"], d["text"], strict=False) if str(w).strip() and float(c) >= 0]
    text = pytesseract.image_to_string(img)
    return text.strip(), (sum(confs) / len(confs) if confs else 0.0)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    data, label = await load_source(ctx, "ocr", args["path"], None)
    sniff(data)  # rejects SVG / non-images before any engine sees the bytes
    local = await asyncio.to_thread(_tesseract, data)
    if local is not None:
        text, conf = local
        return ToolResult(
            f"[ocr engine: tesseract, mean confidence {conf:.1f}]\n{text or '(no text found)'}",
            meta={"engine": "tesseract", "confidence": round(conf, 1)},
        )
    image, _meta = await to_part(data, label)
    answer, model = await ask_vision(ctx, image, TRANSCRIBE)
    return ToolResult(
        f"[ocr engine: vision fallback via {model} (tesseract not available)]\n{answer}",
        meta={"engine": "vision", "model": model},
    )
