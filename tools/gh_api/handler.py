"""`gh api --method GET` on a repository-scoped endpoint (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    endpoint = ghcli.api_endpoint(args["endpoint"], repo)
    argv = ["api", "--method", "GET", endpoint, *ghcli.field_args(args.get("params"))]
    argv += ghcli.flag("hostname", ghcli.host_of(repo))
    argv += ghcli.flag("jq", args.get("jq")) + ghcli.flag("paginate", bool(args.get("paginate")))
    return await ghcli.run_gh(ctx, argv, repo=repo)
