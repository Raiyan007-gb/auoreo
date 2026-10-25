"""Image input for the perception tools: `image` (path or http(s) URL) or `source: "screen"`."""

from __future__ import annotations

from typing import Any

from auoreo.tools.context import ToolContext, ToolError
from auoreo.tools.images import load_source, sniff


async def load_image(ctx: ToolContext, tool: str, args: dict[str, Any]) -> tuple[bytes, str]:
    """`(bytes, label)`. A path is checked as `read` (default allow); a URL as
    `webfetch` (+ SSRF guard); the screen is checked as `screen` (default ask)."""
    image = args.get("image")
    source = args.get("source")
    if bool(image) == bool(source):
        raise ToolError('give exactly one of `image` (path or URL) or `source: "screen"`')
    if source:
        if source != "screen":
            raise ToolError('`source` must be "screen" (use sensor_read for cameras)')
        await ctx.authorize(tool, "screen", "*", default="ask")
        data = await ctx.backend("capture").grab()
        label = "screen capture"
    elif str(image).startswith(("http://", "https://")):
        data, label = await load_source(ctx, tool, None, str(image))
    else:  # local file: the `read` rules (and floors) apply as for the read/ocr tools
        await ctx.authorize(tool, "read", str(ctx.resolve_path(str(image))), default="allow")
        data, label = await load_source(ctx, tool, str(image), None)
    sniff(data)
    return data, label
