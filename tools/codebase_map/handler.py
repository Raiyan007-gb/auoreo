import asyncio
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError

SYMBOL_RE = {
    ".py": re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M),
    ".ts": re.compile(
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|interface|type)\s+(\w+)", re.M
    ),
    ".tsx": re.compile(r"^export\s+(?:default\s+)?(?:function|class|const)\s+(\w+)", re.M),
    ".js": re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function|class)\s+(\w+)", re.M),
    ".go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),
    ".rs": re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait)\s+(\w+)", re.M),
}
ENTRY = {
    "main.py",
    "__main__.py",
    "app.py",
    "manage.py",
    "main.go",
    "main.rs",
    "index.ts",
    "index.js",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
}


async def _files(root: Path) -> list[str]:
    rg = shutil.which("rg")
    if rg is None:

        def walk() -> list[str]:
            return [
                str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts
            ]

        return sorted(await asyncio.to_thread(walk))
    proc = await asyncio.create_subprocess_exec(rg, "--files", cwd=str(root), stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    return sorted(out.decode(errors="replace").splitlines())


async def run(args: dict, ctx: ToolContext) -> str:
    root = ctx.resolve_path(args.get("path") or ".")
    if not root.is_dir():
        raise ToolError(f"not a directory: {root}")
    files = await _files(root)
    stamp = max(((root / f).stat().st_mtime for f in files if (root / f).exists()), default=0)
    key = hashlib.sha256(json.dumps([str(root), len(files), stamp]).encode()).hexdigest()[:16]
    cache = ctx.home / "cache" / "codebase_map" / f"{key}.txt"
    if cache.is_file():
        return cache.read_text()
    exts = Counter(Path(f).suffix or "(none)" for f in files)
    tops = Counter(f.split("/", 1)[0] for f in files if "/" in f)
    entry = [f for f in files if Path(f).name in ENTRY and f.count("/") <= 2]
    tests = sorted({str(Path(f).parent) for f in files if re.search(r"(^|/)(tests?|__tests__|spec)/", f)})
    lines = [
        f"# {root} — {len(files)} files",
        "types: " + ", ".join(f"{e} {n}" for e, n in exts.most_common(12)),
        "top dirs: " + ", ".join(f"{d}/ ({n})" for d, n in tops.most_common(15)),
        "entrypoints: " + (", ".join(entry[:20]) or "none found"),
        "tests: " + (", ".join(tests[:10]) or "none found"),
        "",
        "## symbols",
    ]
    limit = int(args.get("max_files", 300))
    for f in [f for f in files if Path(f).suffix in SYMBOL_RE][:limit]:
        try:
            text = (root / f).read_text(errors="replace")[:200_000]
        except OSError:
            continue
        names = SYMBOL_RE[Path(f).suffix].findall(text)[:25]
        if names:
            lines.append(f"{f}: {', '.join(names)}")
    out = "\n".join(lines)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(out)
    return out
