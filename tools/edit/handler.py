from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.writeguard import check_write


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {path}")
    seen = ctx.state.read_files.get(str(path))
    if seen is None:
        raise ToolError(f"read {path} before editing it")
    if path.stat().st_mtime > seen + 1e-6:
        raise ToolError(f"{path} changed since it was read; read it again")
    text = path.read_text(encoding="utf-8")
    old, new = args["old_string"], args["new_string"]
    if old == new:
        raise ToolError("old_string and new_string are identical")
    count = text.count(old)
    if count == 0:
        raise ToolError("old_string not found in file")
    if count > 1 and not args.get("replace_all"):
        raise ToolError(f"old_string matches {count} times; add context or set replace_all")
    check_write(new, original=text)  # never write a redacted form back (spec §19.10)
    updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8")
    ctx.state.read_files[str(path)] = path.stat().st_mtime
    n = count if args.get("replace_all") else 1
    return ToolResult(f"replaced {n} occurrence(s) in {path}", meta={"replacements": n})
