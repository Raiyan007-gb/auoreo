"""`gh pr comment` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    ghcli.refuse_secrets(body=args["body"])
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["pr", "comment", str(args["number"]), f"--repo={repo}", f"--body={args['body']}"]
    return await ghcli.run_gh(ctx, argv, repo=repo)
