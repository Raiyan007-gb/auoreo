import asyncio
import mimetypes

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import read_capped
from auoreo.voice import voice_for
from auoreo.voice.audio import MAX_INPUT_BYTES


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; the voice service is unavailable")
    path = ctx.resolve_path(args["path"])
    data = await asyncio.to_thread(read_capped, path, MAX_INPUT_BYTES)
    mime = mimetypes.guess_type(path.name)[0]
    res = await voice_for(svc).transcribe(
        data, mime, language=args.get("language"), session_id=ctx.session_id or None
    )
    lang = res.language or "unknown language"
    head = f"Transcript of {path.name} ({res.duration_s:.1f} s, {lang}, {res.backend}):"
    return ToolResult(
        f"{head}\n{res.text}",
        meta={"duration_s": res.duration_s, "backend": res.backend, "segments": len(res.segments)},
    )
