"""tree-sitter outline of a file or a directory (honest error without the `code` extra)."""

import asyncio
import shutil
from pathlib import Path

from auoreo.tools import treesitter
from auoreo.tools.context import ToolContext, ToolError


async def _source_files(root: Path, limit: int) -> list[Path]:
    rg = shutil.which("rg")
    if rg is not None:
        proc = await asyncio.create_subprocess_exec(
            rg, "--files", cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        names = sorted(out.decode(errors="replace").splitlines())
        files = [root / n for n in names]
    else:

        def walk() -> list[Path]:
            return sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)

        files = await asyncio.to_thread(walk)
    return [f for f in files if treesitter.supported(f)][:limit]


def _prune(symbols: list[treesitter.Symbol], depth: int) -> list[treesitter.Symbol]:
    if depth <= 1:
        return [treesitter.Symbol(s.kind, s.name, s.start, s.end) for s in symbols]
    return [treesitter.Symbol(s.kind, s.name, s.start, s.end, _prune(s.children, depth - 1)) for s in symbols]


def _rel(p: Path, cwd: Path) -> str:
    return str(p.relative_to(cwd)) if p.is_relative_to(cwd) else str(p)


async def run(args: dict, ctx: ToolContext) -> str:
    if not treesitter.available():
        raise ToolError(treesitter.MISSING)
    target = ctx.resolve_path(args.get("path") or ".")
    depth = int(args.get("depth", 2))
    cwd = ctx.cwd.resolve()
    if target.is_file():
        if not treesitter.supported(target):
            known = " ".join(sorted(treesitter.GRAMMARS))
            raise ToolError(f"no tree-sitter grammar for '{target.suffix}' (supported: {known})")
        try:
            syms = await asyncio.to_thread(treesitter.outline_file, target)
        except treesitter.TreeSitterMissing as exc:
            raise ToolError(str(exc)) from exc
        lines = treesitter.format_outline(_prune(syms, depth))
        return f"# {_rel(target, cwd)}\n" + ("\n".join(lines) or "(no symbols)")
    if not target.is_dir():
        raise ToolError(f"no such file or directory: {target}")
    limit = int(args.get("max_files", 200))
    files = await _source_files(target, limit)
    out: list[str] = []
    for f in files:
        try:
            syms = await asyncio.to_thread(treesitter.outline_file, f)
        except treesitter.TreeSitterMissing as exc:
            raise ToolError(str(exc)) from exc
        except (OSError, ValueError):
            continue
        if syms:
            out.append(f"# {_rel(f, cwd)}")
            out.extend("  " + ln for ln in treesitter.format_outline(_prune(syms, depth)))
    if not out:
        return f"no supported source files with symbols under {_rel(target, cwd) or '.'}"
    more = f"\n… stopped at max_files={limit}" if len(files) >= limit else ""
    return "\n".join(out) + more
