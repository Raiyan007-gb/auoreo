from auoreo.tools import read_dedupe
from auoreo.tools.context import ToolContext, ToolError, ToolResult

LINE_CUT = 2000


async def run(args: dict, ctx: ToolContext) -> str | ToolResult:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {path}")
    offset, limit = int(args.get("offset", 1)), int(args.get("limit", 2000))
    raw = path.read_bytes()
    if b"\0" in raw[:8192]:
        raise ToolError(f"{path} looks binary; refusing to read")
    lines = raw.decode("utf-8", errors="replace").splitlines()
    ctx.state.read_files[str(path)] = path.stat().st_mtime
    probe = read_dedupe.begin(ctx, path, raw, offset, limit)  # None unless m7.token.read_dedupe
    if probe is not None and (notice := probe.notice()) is not None:
        return notice
    chosen = lines[offset - 1 : offset - 1 + limit]
    out = []
    for no, line in enumerate(chosen, start=offset):
        if len(line) > LINE_CUT:
            line = line[:LINE_CUT] + " …[line truncated]"
        out.append(f"{no:6}\t{line}")
    footer = ""
    if offset - 1 + limit < len(lines):
        footer = f"\n… {len(lines) - (offset - 1 + limit)} more lines (use offset)"
    text = ("\n".join(out) or "(empty file)") + footer
    if probe is not None:
        probe.remember(text)
    return text
