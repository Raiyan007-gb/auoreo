"""Detect and run the project's tests; summarize with `auoreo.tools.testrun` parsers."""

import json
import re
import shlex
import shutil
import uuid
from pathlib import Path

from auoreo.tools import testrun
from auoreo.tools.context import ToolContext, ToolError, ToolResult

RAW_CAP = 200_000
_GO_COV = re.compile(r"coverage: (\d+(?:\.\d+)?%) of statements")


def _text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_pytest(cwd: Path) -> bool:
    if any((cwd / n).is_file() for n in ("pytest.ini", "conftest.py", "tox.ini")):
        return True
    if "[tool.pytest" in _text(cwd / "pyproject.toml") or "[tool:pytest]" in _text(cwd / "setup.cfg"):
        return True
    return any(cwd.glob("test_*.py")) or any(cwd.glob("tests/**/test_*.py"))


def _npm_test(cwd: Path) -> bool:
    try:
        script = json.loads(_text(cwd / "package.json") or "{}").get("scripts", {}).get("test", "")
    except (json.JSONDecodeError, AttributeError):
        return False
    return bool(script) and "no test specified" not in script


DETECT = (
    ("pytest", _has_pytest),
    ("npm", _npm_test),
    ("go", lambda cwd: (cwd / "go.mod").is_file()),
    ("cargo", lambda cwd: (cwd / "Cargo.toml").is_file()),
)


def _which(ctx: ToolContext, name: str) -> bool:
    return shutil.which(name, path=ctx.env.get("PATH", "")) is not None


def _pytest(ctx: ToolContext, a: dict, junit: str) -> tuple[list[str], dict]:
    if (ctx.cwd / "uv.lock").is_file() and _which(ctx, "uv"):
        base = ["uv", "run", "pytest"]
    else:
        base = ["pytest"] if _which(ctx, "pytest") else ["python3", "-m", "pytest"]
    argv = [*base, "-q", "-rfE", "--tb=short", f"--junitxml={junit}"]
    argv += ["-k", a["k"]] if a.get("k") else []
    argv += ["--cov", "--cov-report=term"] if a.get("coverage") else []
    return argv + ([a["path"]] if a.get("path") else []), {}


def _npm(ctx: ToolContext, a: dict, junit: str) -> tuple[list[str], dict]:
    pm = (
        "pnpm"
        if (ctx.cwd / "pnpm-lock.yaml").is_file()
        else "yarn"
        if (ctx.cwd / "yarn.lock").is_file()
        else "npm"
    )
    extra = ([a["path"]] if a.get("path") else []) + (["-t", a["k"]] if a.get("k") else [])
    extra += ["--coverage"] if a.get("coverage") else []
    sep = ["--"] if pm == "npm" and extra else []
    return [pm, "test", *sep, *extra], {"CI": "1", "FORCE_COLOR": "0"}


def _go(ctx: ToolContext, a: dict, junit: str) -> tuple[list[str], dict]:
    pkg = f"./{a['path'].rstrip('/')}/..." if a.get("path") else "./..."
    argv = [
        "go",
        "test",
        "-json",
        *(["-run", a["k"]] if a.get("k") else []),
        *(["-cover"] if a.get("coverage") else []),
    ]
    return [*argv, pkg], {}


def _cargo(ctx: ToolContext, a: dict, junit: str) -> tuple[list[str], dict]:
    return ["cargo", "test", *([a["k"]] if a.get("k") else [])], {}


BUILD = {"pytest": _pytest, "npm": _npm, "go": _go, "cargo": _cargo}


def _summarize(runner: str, out: str, junit: Path) -> testrun.TestSummary:
    if runner == "pytest":
        s = testrun.parse_pytest(out)
        if junit.is_file():
            j = testrun.parse_junit(_text(junit))
            if j.total:
                j.coverage = s.coverage
                s = j
        return s
    if runner == "go":
        s = testrun.parse_go_json(out)
        s.coverage = (m := _GO_COV.findall(out)) and m[-1] or None
        return s
    return testrun.parse_cargo(out) if runner == "cargo" else testrun.parse_jest(out)


def _check_args(ctx: ToolContext, a: dict) -> dict:
    a = dict(a)
    if a.get("path"):
        p = ctx.resolve_path(a["path"])
        root = ctx.cwd.resolve()
        if p != root and root not in p.parents:
            raise ToolError(f"path `{a['path']}` is outside the working directory")
        a["path"] = str(p.relative_to(root)) or "."
    if str(a.get("k", "")).startswith("-"):
        raise ToolError("k must not start with '-'")
    return a


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    a = _check_args(ctx, args)
    runner = a.get("runner", "auto")
    if runner == "auto":
        runner = next((name for name, probe in DETECT if probe(ctx.cwd)), "")
        if not runner:
            raise ToolError(
                "no test runner detected (pytest config/tests, package.json test script, go.mod, Cargo.toml)"
            )
    junit = ctx.cwd / ".pytest_cache" / f"auoreo-junit-{uuid.uuid4().hex[:8]}.xml"
    argv, env = BUILD[runner](ctx, a, str(junit.relative_to(ctx.cwd)))
    if not _which(ctx, argv[0]):
        raise ToolError(f"`{argv[0]}` is not installed or not on PATH; cannot run {runner} tests")
    res = await testrun.execute(ctx, argv, timeout_s=int(a.get("timeout_s", 600)), env=env)
    try:
        summary = _summarize(runner, res.output, junit)
    finally:
        junit.unlink(missing_ok=True)
    head = f"$ {shlex.join(argv)}  (exit {res.exit_code}, {res.seconds:.1f}s)"
    if res.timed_out:
        head += f"\n[timed out after {a.get('timeout_s', 600)}s]"
    body = testrun.render_tests(summary)
    if summary.total == 0:
        body = "result: no test results parsed" + (" (no tests collected)" if res.exit_code == 5 else "")
    if runner == "cargo" and (a.get("path") or a.get("coverage")):
        body += "\nnote: cargo test ignores path/coverage here; use k to filter"
    raw = res.output[-RAW_CAP:]
    text = f"{head}\n{body}\n--- raw output (tail) ---\n{raw}"
    is_error = res.timed_out or res.exit_code != 0 or not summary.ok or summary.total == 0
    meta = {
        "runner": runner,
        "exit_code": res.exit_code,
        "passed": summary.passed,
        "failed": summary.failed,
        "skipped": summary.skipped,
        "errors": summary.errors,
    }
    return ToolResult(text, is_error=is_error, meta=meta)
