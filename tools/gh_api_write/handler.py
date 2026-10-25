"""`gh api --method POST|PATCH|PUT|DELETE` on a repository-scoped endpoint (spec §2.6).
String fields go as `-f` (raw, never `@file`); values that look like secrets are refused."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    endpoint = ghcli.api_endpoint(args["endpoint"], repo)
    fields = args.get("fields") or {}
    ghcli.refuse_secrets(**{f"fields.{k}": v for k, v in fields.items()})
    argv = ["api", "--method", args["method"], endpoint, *ghcli.field_args(fields)]
    argv += ghcli.flag("hostname", ghcli.host_of(repo)) + ghcli.flag("jq", args.get("jq"))
    return await ghcli.run_gh(ctx, argv, repo=repo)
