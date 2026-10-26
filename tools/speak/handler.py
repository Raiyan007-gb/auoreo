import asyncio
import re
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.voice import notes, voice_for
from auoreo.voice.audio import MEDIA_EXT, AudioError, duration_s

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _write(folder: Path, ext: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    n = 1 + sum(1 for p in folder.iterdir() if p.is_file())
    while (path := folder / f"{n}.{ext}").exists():
        n += 1
    path.write_bytes(data)
    return path


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; the voice service is unavailable")
    audio = await voice_for(svc).speak(
        args["text"], voice=args.get("voice"), session_id=ctx.session_id or None
    )
    folder = ctx.home / "sessions" / _SAFE.sub("_", ctx.session_id or "default") / "audio"
    path = await asyncio.to_thread(_write, folder, MEDIA_EXT.get(audio.media_type, "bin"), audio.data)
    seconds = None
    if audio.media_type == "audio/wav":
        try:
            seconds = round(duration_s(audio.data), 2)
        except AudioError:
            seconds = None
    media = notes.envelope(path, audio.media_type, seconds)
    return ToolResult(
        f"Saved speech ({audio.media_type}, {len(audio.data)} bytes) to {path}\n"
        "To deliver it as a voice note (Telegram/Discord bubble, MP3 elsewhere), put this line "
        f"on its own line in your final reply:\n{media}",
        meta={"path": str(path), "media_type": audio.media_type, "bytes": len(audio.data), "media": media},
    )
