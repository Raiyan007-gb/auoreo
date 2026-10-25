"""Read a session-scoped sensor probe (started on first use, stopped at session close)."""

import asyncio
import json

from auoreo.perception.sensors import redact_source, registry_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part

CURRENT = {"current", "self", "this"}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; sensor probes unavailable")
    reg = registry_for(svc)
    name = args.get("probe") or reg.default_probe()
    source = str(args["source"]).strip()
    if name == "rtc" and source in CURRENT:
        source = ctx.session_id
    op = await reg.acquire(ctx.session_id, name, source)
    shown = redact_source(source)
    lines = [f"[sensor: {name} on {shown}]"]
    frame = None
    n = int(args.get("samples", 1))
    rows = []
    for i in range(n):
        if i and args.get("interval_ms"):
            await asyncio.sleep(int(args["interval_ms"]) / 1000)
        s = await reg.read(op)
        reading = json.dumps(s.reading, default=str, sort_keys=True)[:600]
        lines.append(
            f"sample {op.samples}: presence={'yes' if s.presence else 'no'} "
            f"confidence={s.confidence:.2f} reading={reading}"
        )
        rows.append({"presence": s.presence, "confidence": s.confidence, "reading": s.reading})
        frame = s.image or frame
    images = []
    if frame is not None and args.get("image", True):
        part, meta = await to_part(frame, f"{name} frame from {shown}")
        images.append(part)
        lines.append(f"(latest frame attached: {meta['width']}x{meta['height']})")
    if args.get("stop"):
        await reg.release(ctx.session_id, name, source)
        lines.append("(probe stopped)")
    caps = op.probe.capabilities()
    return ToolResult(
        "\n".join(lines), meta={"probe": name, "samples": rows, "capabilities": caps}, images=images
    )
