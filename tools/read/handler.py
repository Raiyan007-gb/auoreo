from auoreo.tools.context import ToolContext, ToolError

LINE_CUT = 2000


async def run(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {path}")
    offset, limit = int(args.get("offset", 1)), int(args.get("limit", 2000))
    raw = path.read_bytes()
    if b"\0" in raw[:8192]:
        raise ToolError(f"{path} looks binary; refusing to read")
    lines = raw.decode("utf-8", errors="replace").splitlines()
    ctx.state.read_files[str(path)] = path.stat().st_mtime
    chosen = lines[offset - 1 : offset - 1 + limit]
    out = []
    for no, line in enumerate(chosen, start=offset):
        if len(line) > LINE_CUT:
            line = line[:LINE_CUT] + " …[line truncated]"
        out.append(f"{no:6}\t{line}")
    footer = ""
    if offset - 1 + limit < len(lines):
        footer = f"\n… {len(lines) - (offset - 1 + limit)} more lines (use offset)"
    return ("\n".join(out) or "(empty file)") + footer
