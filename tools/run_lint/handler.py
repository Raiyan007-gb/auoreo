"""Run the configured linters and summarize them with `auoreo.tools.testrun` parsers."""

import json
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from auoreo.tools import testrun
from auoreo.tools.context import ToolContext, ToolError, ToolResult

Parser = Callable[[str], list[testrun.Finding]]


def _text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _pkg_deps(cwd: Path) -> set[str]:
    try:
        data = json.loads(_text(cwd / "package.json") or "{}")
    except json.JSONDecodeError:
        return set()
    return {*data.get("dependencies", {}), *data.get("devDependencies", {})}


def _any(cwd: Path, *names: str) -> bool:
    return any((cwd / n).exists() for n in names)


DETECT: dict[str, Callable[[Path], bool]] = {
    "ruff": lambda c: "[tool.ruff" in _text(c / "pyproject.toml") or _any(c, "ruff.toml", ".ruff.toml"),
    "mypy": lambda c: (
        "[tool.mypy" in _text(c / "pyproject.toml")
        or _any(c, "mypy.ini", ".mypy.ini")
        or "[mypy" in _text(c / "setup.cfg")
    ),
    "eslint": lambda c: (
        "eslint" in _pkg_deps(c)
        or _any(
            c,
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            "eslint.config.ts",
            ".eslintrc.json",
            ".eslintrc.js",
        )
    ),
    "tsc": lambda c: (c / "tsconfig.json").is_file() and "typescript" in _pkg_deps(c),
}


def _which(ctx: ToolContext, name: str) -> bool:
    return shutil.which(name, path=ctx.env.get("PATH", "")) is not None


def _py(ctx: ToolContext, tool: str) -> list[str] | None:
    if (ctx.cwd / "uv.lock").is_file() and _which(ctx, "uv"):
        return ["uv", "run", tool]
    return [tool] if _which(ctx, tool) else None


def _js(ctx: ToolContext, tool: str) -> list[str] | None:
    local = ctx.cwd / "node_modules" / ".bin" / tool
    if local.exists():
        return [str(local)]
    return ["npx", "--no-install", tool] if _which(ctx, "npx") else None


def _commands(ctx: ToolContext, tool: str, paths: list[str]) -> list[tuple[str, list[str], Parser]] | None:
    """`(label, argv, parser)` per command of one linter, or None when it is not installed."""
    if tool in ("ruff", "mypy"):
        base = _py(ctx, tool)
        if base is None:
            return None
        if tool == "ruff":
            return [
                (
                    "ruff check",
                    [*base, "check", "--output-format=json", "--no-fix", *paths],
                    testrun.parse_ruff_json,
                ),
                ("ruff format", [*base, "format", "--check", *paths], testrun.parse_ruff_format),
            ]
        configured = (
            "files" in _text(ctx.cwd / "pyproject.toml").split("[tool.mypy]", 1)[-1].split("\n[", 1)[0]
        )
        targets = paths or ([] if configured else ["src" if (ctx.cwd / "src").is_dir() else "."])
        return [("mypy", [*base, "--no-color-output", *targets], testrun.parse_mypy)]
    base = _js(ctx, tool)
    if base is None:
        return None
    if tool == "eslint":
        return [("eslint", [*base, "--format", "json", *(paths or ["."])], testrun.parse_eslint_json)]
    return [("tsc", [*base, "--noEmit", "--pretty", "false"], testrun.parse_tsc)]


def _paths(ctx: ToolContext, raw: list[str]) -> list[str]:
    root = ctx.cwd.resolve()
    out = []
    for item in raw:
        p = ctx.resolve_path(item)
        if p != root and root not in p.parents:
            raise ToolError(f"path `{item}` is outside the working directory")
        out.append(str(p.relative_to(root)) or ".")
    return out


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    paths = _paths(ctx, list(args.get("paths") or []))
    wanted = list(args.get("tools") or [t for t, probe in DETECT.items() if probe(ctx.cwd)])
    if not wanted:
        raise ToolError(
            "no linters configured (ruff/mypy in pyproject.toml, eslint config, tsconfig.json + typescript)"
        )
    timeout = int(args.get("timeout_s", 300))
    sections: list[str] = []
    total, failed, meta = 0, False, {}
    for tool in wanted:
        cmds = _commands(ctx, tool, paths)
        if cmds is None:
            sections.append(f"{tool}: skipped (not installed / not on PATH)")
            continue
        for label, argv, parse in cmds:
            res = await testrun.execute(ctx, argv, timeout_s=timeout)
            findings = parse(res.output)
            for f in findings:  # cwd-relative paths read better and cost fewer tokens
                f.file = f.file.removeprefix(f"{ctx.cwd}/").removeprefix(f"{ctx.cwd.resolve()}/")
            total += len(findings)
            failed = failed or res.exit_code != 0 or res.timed_out
            meta[label] = {"exit_code": res.exit_code, "findings": len(findings)}
            text = testrun.render_findings(label, findings, exit_code=res.exit_code)
            if res.timed_out:
                text += f" [timed out after {timeout}s]"
            if res.exit_code != 0 and not findings:
                text += f"\n  $ {shlex.join(argv)}\n" + "\n".join(
                    f"  {ln}" for ln in testrun.tail(res.output, 20).splitlines()
                )
            sections.append(text)
    verdict = "clean" if not failed else f"{total} finding(s)"
    return ToolResult(
        f"lint: {verdict}\n" + "\n".join(sections), is_error=failed, meta={"linters": meta, "findings": total}
    )
