import base64
import time

from auoreo.backends.browser import browser_for
from auoreo.gateway.types import ImagePart
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.images import normalize


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    browser = browser_for(ctx)
    data = await browser.screenshot(ctx.session_id, bool(args.get("full_page", False)))
    folder = ctx.home / "sessions" / ctx.session_id / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 1_000_000:06d}.png"
    path.write_bytes(data)
    # The file keeps the full-resolution PNG; the model gets the size-capped re-encoding.
    encoded, media_type, info = normalize(data)
    note = f"browser screenshot {info.get('width', '?')}x{info.get('height', '?')} ({path.name})"
    meta = {"path": str(path), "bytes": len(data), "media_type": media_type}
    text = f"screenshot saved: {path} ({len(data)} bytes); image attached"
    return ToolResult(
        text,
        meta=meta,
        images=[ImagePart(media_type, base64.b64encode(encoded).decode("ascii"), note)],
    )
