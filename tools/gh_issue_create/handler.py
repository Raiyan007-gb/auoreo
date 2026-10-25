"""`gh issue create` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    labels = list(args.get("labels") or [])
    ghcli.refuse_secrets(title=args["title"], body=args["body"], labels=labels)
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["issue", "create", f"--repo={repo}", f"--title={args['title']}", f"--body={args['body']}"]
    for label in labels:
        argv += ghcli.flag("label", label)
    return await ghcli.run_gh(ctx, argv, repo=repo)
