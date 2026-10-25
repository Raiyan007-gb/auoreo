"""`gh repo view --json` (spec §2.6)."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = (
    "nameWithOwner,description,url,homepageUrl,defaultBranchRef,visibility,isArchived,isFork,"
    "primaryLanguage,stargazerCount,forkCount,latestRelease,updatedAt"
)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    return await ghcli.run_gh(ctx, ["repo", "view", repo, f"--json={FIELDS}"], repo=repo)
