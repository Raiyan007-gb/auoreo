from auoreo.tools.context import ToolContext, ToolError
from auoreo.tools.writeguard import check_write


async def run(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve_path(args["path"])
    if path.exists():
        if path.is_dir():
            raise ToolError(f"{path} is a directory")
        if str(path) not in ctx.state.read_files:
            raise ToolError(f"{path} exists; read it before overwriting")
    check_write(
        args["content"],
        original=path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.auoreo-tmp")
    tmp.write_text(args["content"], encoding="utf-8")
    tmp.replace(path)
    ctx.state.read_files[str(path)] = path.stat().st_mtime
    return f"wrote {len(args['content'])} chars to {path}"
