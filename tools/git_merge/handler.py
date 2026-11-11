"""`git_merge` (M7 W4 Wave 2, `m7.orch.merge`): merge one human-approved commit of an
`auoreo/*` worker branch into the server checkout. The logic and the approval live in
`auoreo.teams.merge_flow`; this handler checks arguments only."""

import re

from auoreo.teams.merge_flow import git_merge
from auoreo.tools.context import ToolContext, ToolResult

BRANCH = re.compile(r"auoreo/[a-z0-9._-]+(/[a-z0-9._-]+)*")
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def pre_check(args: dict) -> str | None:
    """Refused before any prompt: only `auoreo/*` branches, only full commit ids."""
    if not BRANCH.fullmatch(str(args.get("branch", ""))) or ".." in str(args.get("branch")):
        return "git_merge merges only auoreo/<slug> worker branches"
    if not SHA.fullmatch(str(args.get("sha", ""))):
        return "git_merge needs the full commit id (40 or 64 hex characters), not an abbreviation"
    return None


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    note = args.get("note")
    return await git_merge(ctx, args["branch"], args["sha"], note if isinstance(note, str) else None)
