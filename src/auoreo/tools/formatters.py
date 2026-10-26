"""Post-edit format step for `apply_patch` (spec §2.6/§9.3: verify → ask → apply → format →
LSP diagnostics). Fail-open: a missing formatter, a timeout or a formatter error is a note,
never a failed edit.

A formatter only runs when the *project* opts into it, so a stray file never gets reformatted:

- `.py/.pyi` → `ruff format` when an ancestor has `ruff.toml`, `.ruff.toml` or a
  `pyproject.toml` with a `[tool.ruff` table;
- js/ts → `node_modules/.bin/prettier --write` found in an ancestor;
- `.rs` → `rustfmt` when an ancestor has `Cargo.toml` (edition from `rustfmt.toml`, else 2021).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

JS_EXT = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})


@dataclass
class FormatReport:
    formatted: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _ancestors(path: Path) -> list[Path]:
    return [path.parent, *path.parent.parents]


def _ruff_root(path: Path) -> Path | None:
    for d in _ancestors(path):
        if (d / "ruff.toml").is_file() or (d / ".ruff.toml").is_file():
            return d
        py = d / "pyproject.toml"
        if py.is_file():
            with contextlib.suppress(OSError):
                if "[tool.ruff" in py.read_text(encoding="utf-8", errors="replace"):
                    return d
    return None


def _ruff(path: Path) -> tuple[str, Path, list[str]] | None:
    root, exe = _ruff_root(path), shutil.which("ruff")
    return ("ruff", root, [exe, "format", "--quiet"]) if root and exe else None


def _prettier(path: Path) -> tuple[str, Path, list[str]] | None:
    for d in _ancestors(path):
        exe = d / "node_modules" / ".bin" / "prettier"
        if exe.is_file():
            return ("prettier", d, [str(exe), "--write", "--log-level", "warn"])
    return None


def _rustfmt(path: Path) -> tuple[str, Path, list[str]] | None:
    exe = shutil.which("rustfmt")
    root = next((d for d in _ancestors(path) if (d / "Cargo.toml").is_file()), None)
    if exe is None or root is None:
        return None
    has_cfg = (root / "rustfmt.toml").is_file() or (root / ".rustfmt.toml").is_file()
    return ("rustfmt", root, [exe] if has_cfg else [exe, "--edition", "2021"])


Picker = Callable[[Path], tuple[str, Path, list[str]] | None]
PICKERS: dict[str, Picker] = {".py": _ruff, ".pyi": _ruff, ".rs": _rustfmt}
PICKERS.update(dict.fromkeys(JS_EXT, _prettier))


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


async def _run(argv: list[str], cwd: Path, env: dict[str, str], timeout_s: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env or None, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, f"timed out after {timeout_s:g}s"
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def format_files(paths: list[Path], env: dict[str, str], timeout_s: float = 20.0) -> FormatReport:
    """Format the files that exist and have an opted-in formatter; report which changed."""
    groups: dict[tuple[str, Path, tuple[str, ...]], list[Path]] = {}
    for path in paths:
        pick = PICKERS.get(path.suffix)
        if pick is None or not path.is_file():
            continue
        chosen = pick(path)
        if chosen is not None:
            groups.setdefault((chosen[0], chosen[1], tuple(chosen[2])), []).append(path)
    report = FormatReport()
    for (name, root, cmd), files in groups.items():
        before = {p: _digest(p) for p in files}
        try:
            code, out = await _run([*cmd, *map(str, files)], root, env, timeout_s)
        except OSError as exc:
            report.notes.append(f"{name}: could not run ({exc.strerror or exc})")
            continue
        if code != 0:
            report.notes.append(f"{name} exited {code}: {out.splitlines()[0][:200] if out else 'no output'}")
        report.formatted += [p for p in files if _digest(p) != before[p]]
    return report
