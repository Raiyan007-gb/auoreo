"""`gh pr checks --json` (spec §2.6). gh exits 8 while checks are pending and 1 when one
failed; both still print the JSON, so they are not tool errors."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = "name,state,bucket,workflow,link,startedAt,completedAt"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    argv = ["pr", "checks", str(args["number"]), f"--repo={repo}", f"--json={FIELDS}"]
    argv += ghcli.flag("required", bool(args.get("required_only")))
    res = await ghcli.run_gh(ctx, argv, repo=repo, ok_codes=(0, 1, 8))
    if res.meta.get("exit_code") in (1, 8) and not res.content.lstrip().startswith("["):
        res.is_error = True  # a non-JSON failure (no checks, bad PR) also exits 1
    return res
