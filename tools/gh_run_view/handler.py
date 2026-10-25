"""`gh run view` (spec §2.6): a compact job/failed-step summary, or the log tail."""

from auoreo.tools import ghcli
from auoreo.tools.context import ToolContext, ToolResult

FIELDS = "databaseId,displayTitle,status,conclusion,workflowName,headBranch,headSha,event,url,jobs"
JQ = (
    "{id: .databaseId, title: .displayTitle, status, conclusion, workflow: .workflowName, "
    "branch: .headBranch, sha: .headSha, event, url, jobs: [.jobs[]? | {id: .databaseId, name, "
    'status, conclusion, failed_steps: [.steps[]? | select(.conclusion == "failure") | .name]}]}'
)
LOG_FLAG = {"failed": "--log-failed", "all": "--log"}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    repo = await ghcli.repo_for(ctx, args.get("repo"))
    run_id = str(args["run_id"])
    mode = args.get("log", "none")
    if mode == "none":
        argv = ["run", "view", run_id, f"--repo={repo}", f"--json={FIELDS}", f"--jq={JQ}"]
        return await ghcli.run_gh(ctx, argv, repo=repo)
    argv = ["run", "view", run_id, f"--repo={repo}", LOG_FLAG[mode]]
    res = await ghcli.run_gh(ctx, argv, repo=repo, cap=10**8, timeout_s=110)
    if not res.is_error:
        res.content = ghcli.clip(ghcli.tail_lines(res.content, int(args.get("tail_lines", 200))), 30_000)
    return res
