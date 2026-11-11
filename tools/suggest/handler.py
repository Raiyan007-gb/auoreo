"""Propose a consent-first suggestion. Never executes anything: the user must accept it."""

from auoreo.suggestions import SuggestionError, suggestions_of
from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("suggestions are not available (no services attached)")
    action: dict = {}
    if args.get("prompt") and args.get("kanban"):
        raise ToolError("give either `prompt` or `kanban`, not both")
    if args.get("kanban"):
        action = {"type": "kanban_create", **args["kanban"]}
    elif args.get("prompt"):
        action = {"type": "prompt", "text": args["prompt"]}
        if args.get("agent"):
            action["agent"] = args["agent"]
    try:
        row, created = await suggestions_of(svc).propose(
            args.get("kind", "info"),
            args["title"],
            args.get("body", ""),
            deliver=args.get("deliver", "origin"),
            origin_session=ctx.session_id,
            action=action,
            dedupe_key=args.get("dedupe_key"),
            ttl_s=int(args.get("ttl_s", 86400)),
        )
    except SuggestionError as exc:
        raise ToolError(str(exc)) from exc
    if not created:
        return ToolResult(
            f"already pending as suggestion #{row.id}: {row.title}", meta={"id": row.id, "created": False}
        )
    where = "" if row.deliver == "origin" else f", delivery to {row.deliver} queued"
    return ToolResult(
        f"proposed suggestion #{row.id} (pending user consent{where}): {row.title}",
        meta={"id": row.id, "created": True},
    )
