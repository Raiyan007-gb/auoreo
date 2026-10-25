"""`gh issue view --json` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = "number,title,state,author,labels,assignees,url,createdAt,updatedAt,body"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    fields = FIELDS + (",comments" if args.get("comments") else "")
    argv = ["issue", "view", str(args["number"]), f"--repo={repo}", f"--json={fields}"]
    return await ghcli.run_gh(ctx, argv, repo=repo)
