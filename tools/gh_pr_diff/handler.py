"""`gh pr diff` (spec §2.6); large diffs are clipped head + tail."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["pr", "diff", str(args["number"]), f"--repo={repo}", "--color=never"]
    argv += ghcli.flag("name-only", bool(args.get("name_only")))
    return await ghcli.run_gh(ctx, argv, repo=repo, cap=30_000)
