"""`gh pr list --json` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = "number,title,state,author,headRefName,baseRefName,isDraft,updatedAt,url"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["pr", "list", f"--repo={repo}", f"--state={args.get('state', 'open')}"]
    argv += [f"--limit={args.get('limit', 20)}", f"--json={FIELDS}"]
    for key in ("author", "label", "base", "search"):
        argv += ghcli.flag(key, args.get(key))
    return await ghcli.run_gh(ctx, argv, repo=repo)
