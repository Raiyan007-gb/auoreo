"""`gh pr create` (spec §2.6). Push the head branch first; gh never prompts here."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    ghcli.refuse_secrets(title=args["title"], body=args["body"])
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["pr", "create", f"--repo={repo}", f"--title={args['title']}", f"--body={args['body']}"]
    argv += ghcli.flag("base", args.get("base")) + ghcli.flag("head", args.get("head"))
    argv += ghcli.flag("draft", bool(args.get("draft")))
    return await ghcli.run_gh(ctx, argv, repo=repo)
