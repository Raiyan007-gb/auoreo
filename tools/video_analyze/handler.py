"""video_analyze (spec §14.1): ffmpeg frame sampling (no Python video deps) + ONE vision aux
call with every frame attached. ffmpeg missing → an honest error naming the fix."""

import asyncio
import re
import shutil
from pathlib import Path

from auoreo.core.settings_h5 import h5_of
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import LLMRequest, TextDelta
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part, vision_model

EXTENSIONS = frozenset({".mp4", ".m4v", ".webm", ".mov", ".avi", ".mkv", ".mpeg", ".mpg"})
DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
FFMPEG_HINT = "install ffmpeg (apt install ffmpeg · brew install ffmpeg · winget install Gyan.FFmpeg)"


async def _exec(argv: list[str], timeout_s: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ToolError(f"ffmpeg timed out after {timeout_s:g}s") from exc
    return proc.returncode or 0, out, err


async def duration_s(ffmpeg: str, path: Path, timeout_s: float) -> float:
    _, _, err = await _exec([ffmpeg, "-hide_banner", "-i", str(path)], timeout_s)
    m = DURATION.search(err.decode(errors="replace"))
    if not m:
        raise ToolError(f"{path.name}: ffmpeg could not read a duration (not a video, or unsupported codec)")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def timestamps(duration: float, n: int) -> list[float]:
    """`n` evenly spaced points strictly inside the clip (centres of n equal slices)."""
    return [round(duration * (i + 0.5) / n, 3) for i in range(n)]


async def frame(ffmpeg: str, path: Path, at: float, timeout_s: float) -> bytes:
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(path)]
    argv += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    code, out, err = await _exec(argv, timeout_s)
    if code != 0 or not out:
        raise ToolError(
            f"ffmpeg could not extract a frame at {at:.1f}s: {err.decode(errors='replace')[:200]}"
        )
    return out


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    cfg = h5_of(ctx).video
    path = ctx.resolve_path(args["path"])
    if path.suffix.lower() not in EXTENSIONS:
        kinds = ", ".join(sorted(EXTENSIONS))
        raise ToolError(f"unsupported video type {path.suffix or '(none)'}; use one of {kinds}")
    if not path.is_file():
        raise ToolError(f"file not found: {path}")
    size = path.stat().st_size
    if size > cfg.max_bytes:
        raise ToolError(f"{path.name} is {size / 2**20:.1f} MB; the limit is {cfg.max_bytes / 2**20:.0f} MB")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ToolError(f"video_analyze needs ffmpeg, which is not installed: {FFMPEG_HINT}")
    model = vision_model(ctx)  # fail before the (slow) sampling when nothing can see
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cfg.timeout_s
    total = await duration_s(ffmpeg, path, cfg.timeout_s)
    n = min(int(args.get("frames") or cfg.max_frames), cfg.max_frames)
    points = timestamps(total, n) if total > 0 else [0.0]
    content: list = [{"type": "text", "text": args["question"]}]
    for at in points:
        left = deadline - loop.time()
        if left <= 0:
            raise ToolError(f"frame sampling exceeded {cfg.timeout_s:g}s")
        image, _ = await to_part(await frame(ffmpeg, path, at, left), f"frame at {at:.1f}s")
        content += [{"type": "text", "text": f"[frame at {at:.1f}s of {total:.1f}s]"}, image.part()]
    answer = await _ask(ctx, model, content)
    note = f"\n(note: {size / 2**20:.0f} MB video; large files are slow)" if size > cfg.warn_bytes else ""
    meta = {"model": model, "frames": len(points), "duration_s": total}
    return ToolResult(answer + note, meta=meta)


async def _ask(ctx: ToolContext, model: str, content: list) -> str:
    svc = ctx.services["services"]
    request = LLMRequest(
        model=model,
        messages=(
            {
                "role": "system",
                "content": "You answer questions about a video from its sampled frames (in time order), "
                "precisely and briefly. Say when the frames cannot answer.",
            },
            {"role": "user", "content": content},
        ),
        tool_choice="none",
        metadata={"purpose": "vision", "session_id": ctx.session_id},
    )
    parts: list[str] = []
    try:
        async for item in svc.gateway.stream(request):
            if isinstance(item, TextDelta):
                parts.append(item.text)
    except GatewayError as exc:
        raise ToolError(f"vision call to '{model}' failed ({exc.code}): {exc.message}") from exc
    answer = "".join(parts).strip()
    if not answer:
        raise ToolError(f"vision model '{model}' returned no text")
    return answer
