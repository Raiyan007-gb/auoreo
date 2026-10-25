"""`verify_plan` gate (spec §2.3): deterministic checks over `ctx.state.plan`, no LLM.

Fails on: an empty plan, a step with no acceptance criterion (`done_when`, or a
backticked command in the step), a path outside the working directory, duplicate steps,
unresolved questions. Destructive steps are listed as needing approval (not a failure).
A pass records the plan hash; `exit_plan_mode` checks it when `plan_requires_verify` is on.
"""

import re
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.testrun import plan_hash

_COMMAND = re.compile(r"`[^`\s][^`]*`")
_PATH = re.compile(r"(?:^|[\s`'\"(=])((?:~|\.\.)?/[^\s`'\"(),;]*|\.\./[^\s`'\"(),;]*)")
_DESTRUCTIVE = re.compile(
    r"\b(delete|remove|rm\s+-|drop\s+(table|database|column)|truncate|force[- ]push|push\s+--force|"
    r"reset\s+--hard|wipe|purge|overwrite|migrat(e|ion)|deploy|publish|release|merge)\b",
    re.I,
)
_QUESTION = re.compile(r"\?\s*$|\bTBD\b|\bTODO\b|\?\?|\bopen question\b|\bunclear\b|\bto be decided\b", re.I)


def _outside(token: str, cwd: Path) -> bool:
    raw = token.rstrip(".:")
    if raw.startswith("~"):
        p = Path(raw).expanduser()
    elif raw.startswith("/"):
        parts = Path(raw).parts
        if len(parts) < 2 or not Path("/", parts[1]).exists():
            return False  # `/api/events` and other non-filesystem slashes
        p = Path(raw)
    else:
        p = cwd / raw
    root = cwd.resolve()
    rp = p.resolve()
    return rp != root and root not in rp.parents


def check(plan: list[dict], cwd: Path) -> tuple[bool, list[str], list[str]]:
    """`(passed, checklist lines, approval-needed lines)`."""
    lines: list[str] = []
    if not plan:
        return False, ["[ ] plan recorded — no plan yet: call update_plan with the steps first"], []
    no_check, outside, dupes, questions, destructive = [], [], [], [], []
    seen: dict[str, int] = {}
    for i, step in enumerate(plan, 1):
        text = str(step.get("step", ""))
        done = str(step.get("done_when") or step.get("check") or "").strip()
        if not done and not _COMMAND.search(text):
            no_check.append(f"step {i}")
        for m in _PATH.finditer(f"{text} {done}"):
            if _outside(m.group(1), cwd):
                outside.append(f"step {i}: {m.group(1)}")
        key = re.sub(r"\s+", " ", text.strip().lower())
        if key in seen:
            dupes.append(f"step {i} repeats step {seen[key]}")
        seen.setdefault(key, i)
        if _QUESTION.search(text) or _QUESTION.search(done):
            questions.append(f"step {i}: {text[:120]}")
        if _DESTRUCTIVE.search(text):
            destructive.append(f"step {i}: {text[:120]}")

    def row(ok: bool, label: str, items: list[str]) -> str:
        return f"[{'x' if ok else ' '}] {label}" + ("" if ok else " — " + "; ".join(items[:10]))

    lines.append(row(True, f"plan recorded ({len(plan)} steps)", []))
    criterion = "every step has an acceptance criterion (done_when or a `command`)"
    lines.append(row(not no_check, criterion, no_check))
    lines.append(row(not outside, "no step references a path outside the working directory", outside))
    lines.append(row(not dupes, "no duplicate steps", dupes))
    lines.append(row(not questions, "no unresolved questions", questions))
    passed = not (no_check or outside or dupes or questions)
    return passed, lines, destructive


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    plan = list(ctx.state.plan)
    passed, lines, destructive = check(plan, ctx.cwd)
    digest = plan_hash(plan)
    if destructive:
        lines.append("needs approval when executed (destructive):")
        lines += [f"  - {d}" for d in destructive]
    ctx.state.plan_verified = digest if passed and plan else None
    verdict = "PASS" if passed else "FAIL — fix the plan with update_plan and call verify_plan again"
    text = "\n".join([f"verify_plan: {verdict}", *lines, f"plan hash: {digest}"])
    meta = {"passed": passed, "hash": digest, "destructive": len(destructive)}
    return ToolResult(text, is_error=not passed, meta=meta)
