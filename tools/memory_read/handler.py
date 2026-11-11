from auoreo.memory.files import FILES, memory_dir
from auoreo.memory.recall import TOPICS_REFUSED, tenant_of
from auoreo.memory.topics import list_topics, read_file
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    name = (args.get("name") or "").strip()
    tenanted = tenant_of(ctx.services) is not None  # topics are shared: never for a tenanted agent
    if not name:
        mdir = memory_dir(ctx.home)
        lines = [f"- {f} ({(mdir / f).stat().st_size} bytes)" for f in FILES if (mdir / f).is_file()]
        lines += [
            f"- {t['file']} [{t.get('type', '?')}] — {t.get('description', '')}"
            for t in ([] if tenanted else list_topics(ctx.home))
        ]
        return "memory files:\n" + ("\n".join(lines) or "- (none)")
    if tenanted and name not in FILES:
        raise ToolError(TOPICS_REFUSED)
    try:
        return read_file(ctx.home, name)
    except FileNotFoundError:
        topics = ", ".join(t["file"] for t in list_topics(ctx.home)) or "none"
        raise ToolError(f"no memory file '{name}'. Files: {', '.join(FILES)}; topics: {topics}") from None
    except ValueError as exc:
        raise ToolError(str(exc)) from None
