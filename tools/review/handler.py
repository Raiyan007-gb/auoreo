"""review (spec §25.15 review gates): `{working_diff, diagnostics} → {blockingErrors[],
preventContinuation}`, optionally followed by the reviewer agent (the `/review` command's
prompt, run as a foreground subagent through the teams extension)."""

import asyncio
import json
import re
import shutil
from pathlib import Path

from auoreo.tools import testrun
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.lsp_diagnostics import diagnostics_for

CONFLICT = re.compile(r"^(<{7}|>{7})( |$)", re.M)
MAX_ROWS = 40


async def _cmd(argv: list[str], cwd: Path, timeout_s: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out after {timeout_s:g}s"
    return proc.returncode or 0, out.decode(errors="replace")


async def working_diff(cwd: Path) -> tuple[str, list[str]]:
    """`git diff HEAD` (or the staged diff of an unborn branch) + changed/untracked paths."""
    git = shutil.which("git")
    if git is None:
        raise ToolError("review needs git on PATH")
    code, top = await _cmd([git, "rev-parse", "--show-toplevel"], cwd)
    if code != 0:
        raise ToolError(f"not a git repository: {cwd}")
    _, status = await _cmd([git, "status", "--porcelain", "--untracked-files=all"], cwd)
    files = [ln[3:].split(" -> ")[-1].strip('"') for ln in status.splitlines() if len(ln) > 3]
    code, diff = await _cmd([git, "diff", "HEAD"], cwd)
    if code != 0:  # no HEAD yet
        _, diff = await _cmd([git, "diff", "--cached"], cwd)
    return diff, files


async def diagnostics(ctx: ToolContext, cwd: Path, files: list[str]) -> tuple[list[str], list[str]]:
    """(blocking errors, advisory notes)."""
    blocking: list[str] = []
    notes: list[str] = []
    git = shutil.which("git") or "git"
    code, check = await _cmd([git, "diff", "HEAD", "--check"], cwd)
    if code not in (0, 128) and check.strip():
        blocking += [f"git diff --check: {ln}" for ln in check.splitlines() if ln and not ln.startswith("+")]
    paths = [cwd / f for f in files if (cwd / f).is_file()]
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if CONFLICT.search(text):
            blocking.append(f"{p.relative_to(cwd)}: unresolved merge conflict markers")
    py = [str(p) for p in paths if p.suffix in (".py", ".pyi")]
    ruff = shutil.which("ruff")
    has_cfg = any(
        (d / "ruff.toml").is_file() or "[tool.ruff" in _read(d / "pyproject.toml")
        for d in [cwd, *cwd.parents]
    )
    if py and ruff and has_cfg:
        _, out = await _cmd([ruff, "check", "--output-format", "json", "--no-fix", *py], cwd, 120)
        for f in testrun.parse_ruff_json(out):
            blocking.append(("ruff " + f.render()).replace(str(cwd) + "/", ""))
    for row in await diagnostics_for(ctx, paths):
        (blocking if " error: " in row else notes).append(f"lsp {row}")
    return blocking[:MAX_ROWS], notes[:MAX_ROWS]


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    except OSError:
        return ""


async def _agent(ctx: ToolContext, focus: str, report: str) -> tuple[str, bool]:
    from auoreo.teams.tooling import attempt, teams_for

    cmd = ctx.harness.commands.get("review") if ctx.harness is not None else None
    body = cmd.template if cmd is not None else "Review: {{args}}"
    prompt = body.replace("{{args}}", focus or "the uncommitted changes").replace(
        "{{worktree}}", str(ctx.cwd)
    )
    prompt += f"\n\nDo not modify any file. Static findings so far:\n{report}"
    agent = (cmd.manifest.agent if cmd is not None else None) or "reviewer"
    member = await attempt(
        teams_for(ctx).spawn(ctx.session_id, agent=agent, prompt=prompt, name="review", background=False)
    )
    return member.notification(), member.status == "completed"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    cwd = ctx.cwd.resolve()
    diff, files = await working_diff(cwd)
    if not files and not diff.strip():
        structured = {"blockingErrors": [], "preventContinuation": False, "files": []}
        return ToolResult("nothing to review: the working tree is clean", meta={"structured": structured})
    blocking, notes = await diagnostics(ctx, cwd, files)
    cap = int(args.get("max_diff_chars", 20000))
    shown = diff if len(diff) <= cap else diff[:cap] + f"\n… [{len(diff) - cap} more diff chars]"
    lines = [f"**review** · {len(files)} changed file(s): {', '.join(files[:30])}"]
    lines.append("blocking errors:" if blocking else "blocking errors: none")
    lines += [f"- {b}" for b in blocking]
    if notes:
        lines += ["notes:", *(f"- {n}" for n in notes)]
    report = "\n".join(lines)
    text = f"{report}\n\n```diff\n{shown}\n```"
    agent_ok = True
    if args.get("mode") == "agent":
        verdict, agent_ok = await _agent(ctx, str(args.get("focus") or ""), report)
        text += f"\n\nreviewer agent:\n{verdict}"
    structured = {"blockingErrors": blocking, "preventContinuation": bool(blocking), "files": files}
    text += "\n\n" + json.dumps({"preventContinuation": structured["preventContinuation"]})
    return ToolResult(text, is_error=not agent_ok, meta={"structured": structured})
