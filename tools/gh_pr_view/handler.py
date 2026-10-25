"""`gh pr view --json` for one pull request (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolError, ToolResult

FIELDS = (
    "number,title,state,author,url,isDraft,baseRefName,headRefName,mergeable,reviewDecision,"
    "labels,additions,deletions,changedFiles,createdAt,updatedAt,body"
)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    number = args.get("number")
    if number is None and args.get("repo") and repo != await ghcli.cwd_repo(ctx.cwd):
        raise ToolError("number is required for a repo other than the working directory's")
    fields = FIELDS + (",comments" if args.get("comments") else "")
    # gh refuses --repo without an argument; no number means the cwd checkout's branch PR.
    target = [str(number), f"--repo={repo}"] if number else []
    argv = ["pr", "view", *target, f"--json={fields}"]
    return await ghcli.run_gh(ctx, argv, repo=repo)
