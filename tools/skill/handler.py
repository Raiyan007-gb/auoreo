from auoreo.harness.skill_sources import find_skill, skill_message
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    spec = ctx.services.get("spec")
    skill = find_skill(spec, ctx.harness, args["name"])
    if skill is None:
        names = (
            [s.name for s in spec.skills]
            if spec is not None
            else list(ctx.harness.all_skills if ctx.harness else {})
        )
        raise ToolError(f"unknown skill '{args['name']}'. Available: {', '.join(sorted(names)) or 'none'}")
    try:
        message = await skill_message(skill)
    except Exception as exc:  # e.g. the MCP server that offers it went away
        raise ToolError(f"could not load skill '{skill.name}' from {skill.source}: {exc}") from exc
    # Skills are injected as user messages (never system edits) to keep the prompt cache-stable.
    ctx.state.pending_user_messages.append(message)
    return f"Loaded skill '{skill.name}' ({skill.source}). Its instructions follow as a message."
