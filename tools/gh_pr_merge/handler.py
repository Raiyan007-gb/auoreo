"""`gh pr merge --squash [--auto]` (spec §2.6). Only squash/auto flags exist; a human
approves every call, even when a rule or mode would allow it."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    number = str(args["number"])
    auto = bool(args.get("auto", True))
    detail = f"{'enable auto-merge (squash) for' if auto else 'squash-merge'} {repo}#{number}"
    await ghcli.human_confirm(ctx, "gh_pr_merge", "github_write", args.get("repo") or "*", detail)
    argv = ["pr", "merge", number, f"--repo={repo}", "--squash", *ghcli.flag("auto", auto)]
    return await ghcli.run_gh(ctx, argv, repo=repo)
