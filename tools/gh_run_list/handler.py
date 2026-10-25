"""`gh run list --json` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = "databaseId,displayTitle,workflowName,status,conclusion,headBranch,event,createdAt,url"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["run", "list", f"--repo={repo}", f"--limit={args.get('limit', 20)}", f"--json={FIELDS}"]
    for key in ("branch", "workflow", "status", "event"):
        argv += ghcli.flag(key, args.get(key))
    return await ghcli.run_gh(ctx, argv, repo=repo)
