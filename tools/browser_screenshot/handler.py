import base64
import time

from auoreo.backends.browser import browser_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    browser = browser_for(ctx)
    data = await browser.screenshot(ctx.session_id, bool(args.get("full_page", False)))
    folder = ctx.home / "sessions" / ctx.session_id / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 1_000_000:06d}.png"
    path.write_bytes(data)
    meta = {"path": str(path), "bytes": len(data), "media_type": "image/png"}
    text = f"screenshot saved: {path} ({len(data)} bytes)"
    if hasattr(ToolResult(""), "images"):  # image-capable results (M4a); else the path suffices
        image = {"media_type": "image/png", "data": base64.b64encode(data).decode("ascii")}
        return ToolResult(text, meta=meta, images=[image])  # type: ignore[call-arg]
    return ToolResult(text, meta=meta)
