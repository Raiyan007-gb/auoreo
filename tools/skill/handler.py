from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    spec = ctx.services.get("spec")
    allowed = (
        {s.name: s for s in spec.skills} if spec is not None else (ctx.harness.skills if ctx.harness else {})
    )
    skill = allowed.get(args["name"])
    if skill is None:
        raise ToolError(f"unknown skill '{args['name']}'. Available: {', '.join(sorted(allowed)) or 'none'}")
    files = sorted(
        str(p.relative_to(skill.path.parent))
        for p in skill.path.parent.rglob("*")
        if p.is_file() and p.name != "SKILL.md"
    )[:50]
    extra = f"\nSupporting files in {skill.path.parent}: {', '.join(files)}" if files else ""
    # Skills are injected as user messages (never system edits) to keep the prompt cache-stable.
    ctx.state.pending_user_messages.append(
        f'<skill name="{skill.name}">\n{skill.body().strip()}{extra}\n</skill>'
    )
    return f"Loaded skill '{skill.name}'. Its instructions follow as a message."
