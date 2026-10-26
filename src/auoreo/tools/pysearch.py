"""Pure-Python content search: the `grep` tool's fallback when ripgrep (`rg`) is missing.

Mirrors the rg flags the tool uses (`--hidden --glob !.git`, `-n`/`-l`/`-c`, `-i`, `-C`,
`--glob`): hidden files are searched, `.git` never is, `.gitignore` files (and
`.git/info/exclude`) are honoured per directory, binary files (a NUL byte in the first 8 KiB)
and files over `max_file_bytes` are skipped, and the walk stops after `max_files` files.
Patterns are Python `re` (close to Rust regex syntax for everyday use).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SNIFF = 8192


def glob_regex(pattern: str) -> str:
    """gitignore/rg glob → regex body (`**` spans directories, `*`/`?` stay inside one)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : end].replace("\\", "\\\\")
                out.append(f"[{'^' + body[1:] if body.startswith('!') else body}]")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class IgnoreRule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool


def parse_ignore(text: str) -> list[IgnoreRule]:
    rules: list[IgnoreRule] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        line = line.removeprefix("\\")
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        body = glob_regex(line.lstrip("/"))
        rx = f"^{body}(?:/.*)?$" if anchored else f"^(?:.*/)?{body}(?:/.*)?$"
        rules.append(IgnoreRule(re.compile(rx), negate, dir_only))
    return rules


@dataclass
class Ignores:
    """Stack of (base dir, rules); later (deeper) files win, last matching rule wins."""

    layers: list[tuple[Path, list[IgnoreRule]]] = field(default_factory=list)

    def ignored(self, path: Path, is_dir: bool) -> bool:
        verdict = False
        for base, rules in self.layers:
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                continue
            for rule in rules:
                if rule.dir_only and not is_dir and not _under_dir_match(rule, rel):
                    continue
                if rule.regex.match(rel):
                    verdict = not rule.negate
        return verdict

    def push(self, directory: Path) -> Ignores:
        f = directory / ".gitignore"
        if not f.is_file():
            return self
        try:
            rules = parse_ignore(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return self
        return Ignores([*self.layers, (directory, rules)])


def _under_dir_match(rule: IgnoreRule, rel: str) -> bool:
    """A dir-only rule still ignores files *below* a matching directory."""
    parts = rel.split("/")
    return any(rule.regex.match("/".join(parts[:i])) for i in range(1, len(parts)))


@dataclass
class SearchOptions:
    pattern: str
    mode: str = "content"  # content | files | count
    case_insensitive: bool = False
    glob: str | None = None
    context: int = 0
    max_file_bytes: int = 4 * 2**20
    max_files: int = 50_000
    max_lines: int = 100_000


def _glob_match(glob: str | None, rel: str) -> bool:
    if not glob:
        return True
    negate = glob.startswith("!")
    pat = glob[1:] if negate else glob
    target = rel if "/" in pat else rel.rsplit("/", 1)[-1]
    hit = re.fullmatch(glob_regex(pat), target) is not None
    return hit != negate


def _root_ignores(root: Path) -> Ignores:
    ign = Ignores()
    cur = root if root.is_dir() else root.parent
    chain = [cur, *cur.parents]
    repo = next((d for d in chain if (d / ".git").exists()), None)
    if repo is not None:
        exclude = repo / ".git" / "info" / "exclude"
        if exclude.is_file():
            ign.layers.append((repo, parse_ignore(exclude.read_text(encoding="utf-8", errors="replace"))))
        for d in reversed(chain[: chain.index(repo) + 1]):
            if d != cur:
                ign = ign.push(d)
    return ign


def iter_files(root: Path, opts: SearchOptions) -> list[Path]:
    if root.is_file():
        return [root]
    out: list[Path] = []
    stack: list[tuple[Path, Ignores]] = [(root, _root_ignores(root).push(root))]
    while stack and len(out) < opts.max_files:
        directory, ign = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            continue
        subdirs: list[tuple[Path, Ignores]] = []
        for entry in entries:
            path = Path(entry.path)
            if entry.name == ".git":
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            if ign.ignored(path, is_dir):
                continue
            if is_dir:
                subdirs.append((path, ign.push(path)))
            elif entry.is_file() and _glob_match(opts.glob, path.relative_to(root).as_posix()):
                out.append(path)
                if len(out) >= opts.max_files:
                    break
        stack.extend(reversed(subdirs))
    return out


def _read_text(path: Path, cap: int) -> str | None:
    try:
        if path.stat().st_size > cap:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:SNIFF]:
        return None
    return data.decode("utf-8", errors="replace")


def search(root: Path, opts: SearchOptions) -> list[str]:
    """rg-shaped output lines (absolute paths; the caller strips its prefix)."""
    flags = re.IGNORECASE if opts.case_insensitive else 0
    rx = re.compile(opts.pattern, flags)
    single = root.is_file()
    lines: list[str] = []
    for path in iter_files(root, opts):
        text = _read_text(path, opts.max_file_bytes)
        if text is None:
            continue
        rows = text.splitlines()
        hits = [i for i, row in enumerate(rows) if rx.search(row)]
        if not hits:
            continue
        name = str(path)
        if opts.mode == "files":
            lines.append(name)
        elif opts.mode == "count":
            lines.append(str(len(hits)) if single else f"{name}:{len(hits)}")
        else:
            lines.extend(_content(rows, hits, "" if single else name, opts.context))
        if len(lines) >= opts.max_lines:
            break
    return lines


def _content(rows: list[str], hits: list[int], name: str, ctx: int) -> list[str]:
    prefix = f"{name}:" if name else ""
    if ctx <= 0:
        return [f"{prefix}{i + 1}:{rows[i]}" for i in hits]
    hit_set = set(hits)
    keep: list[int] = sorted({j for i in hits for j in range(max(0, i - ctx), min(len(rows), i + ctx + 1))})
    out: list[str] = []
    prev = -2
    for j in keep:
        if prev >= 0 and j != prev + 1:
            out.append("--")
        sep = ":" if j in hit_set else "-"
        out.append(f"{name}{sep if name else ''}{j + 1}{sep}{rows[j]}")
        prev = j
    return out
