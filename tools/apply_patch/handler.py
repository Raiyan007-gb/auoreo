"""Codex/V4A patch format (`*** Begin Patch` … `*** End Patch`), parsed with a Lark grammar
(grammar.lark) and applied transactionally: every operation is validated before any file is
written. Afterwards LSP diagnostics for the touched files are appended (fail-open; see
`auoreo.tools.lsp_diagnostics`).

V4A details: stacked `@@ <context>` headers anchor a hunk after those lines (e.g. `@@ class
A` then `@@ def f():`); hunks of one file are located in order, each after the previous;
`*** End of File` anchors a hunk at the end; a hunk with no context/removed lines appends;
blank patch lines count as empty context lines. Matching tries exact text, then ignoring
trailing whitespace, then surrounding whitespace, then normalised Unicode punctuation.
A `apply_patch <<'EOF'` heredoc wrapper around the patch is tolerated.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lark import Lark, Token, Tree
from lark.exceptions import LarkError

from auoreo.core.permissions import is_sensitive_write
from auoreo.core.settings_h5 import h5_of
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.formatters import FormatReport, format_files
from auoreo.tools.lsp_diagnostics import diagnostics_for
from auoreo.tools.writeguard import check_write

_PARSER = Lark((Path(__file__).parent / "grammar.lark").read_text(), parser="lalr")
_SECRET = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.db)$")
_PUNCT = str.maketrans(
    {
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    }
)  # fmt: skip
_FUZZ: list[tuple[str, Callable[[str], str]]] = [
    ("exact", lambda s: s),
    ("trailing whitespace ignored", str.rstrip),
    ("whitespace ignored", str.strip),
    ("unicode punctuation normalised", lambda s: s.translate(_PUNCT).strip()),
]


@dataclass
class Hunk:
    headers: list[str] = field(default_factory=list)
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    eof: bool = False


@dataclass
class Op:
    kind: str
    path: str
    move_to: str | None = None
    lines: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


def _text(node: Tree | Token) -> str:
    return str(node) if isinstance(node, Token) else ""


def _unwrap(patch: str) -> str:
    """Drop a heredoc/shell wrapper (`apply_patch <<'EOF' … EOF`) around the patch."""
    start, end = patch.find("*** Begin Patch"), patch.rfind("*** End Patch")
    if start > 0 and end > start:
        return patch[start : end + len("*** End Patch")] + "\n"
    return patch


def _hunk(tree: Tree) -> Hunk:
    hunk = Hunk()
    kinds: list[str] = []
    for child in tree.children:
        if not isinstance(child, Tree):
            continue
        if child.data == "header":
            line = next((str(c) for c in child.children if isinstance(c, Token) and c.type == "LINE"), "")
            hunk.headers.append(line.strip())
        elif child.data == "eof":
            hunk.eof = True
        elif child.data == "hunk_line":
            tok = child.children[0]
            assert isinstance(tok, Token)
            kinds.append(tok.type)
            body = "" if tok.type == "NL" else str(tok)[1:]
            if tok.type in {"CONTEXT", "REMOVE", "NL"}:
                hunk.old.append(body)
            if tok.type in {"CONTEXT", "ADDED", "NL"}:
                hunk.new.append(body)
    while kinds and kinds[-1] == "NL":  # blank separator lines after the last change
        kinds.pop()
        hunk.old.pop()
        hunk.new.pop()
    return hunk


def parse(patch: str) -> list[Op]:
    patch = _unwrap(patch)
    if not patch.endswith("\n"):
        patch += "\n"
    try:
        tree = _PARSER.parse(patch)
    except LarkError as exc:
        raise ToolError(f"patch does not match the format: {str(exc).splitlines()[0]}") from exc
    ops: list[Op] = []
    for op_tree in tree.find_data("op"):
        node = op_tree.children[0]
        assert isinstance(node, Tree)
        path = next(_text(c) for c in node.children if isinstance(c, Token) and c.type == "PATH").strip()
        op = Op(str(node.data), path)
        if node.data == "add":
            for line in node.find_data("add_line"):
                op.lines.append(next((str(c) for c in line.children if c.type == "LINE"), ""))
        elif node.data == "update":
            for child in node.children:
                if isinstance(child, Tree) and child.data == "move":
                    op.move_to = next(str(c) for c in child.children if c.type == "PATH").strip()
                elif isinstance(child, Tree) and child.data == "hunk":
                    op.hunks.append(_hunk(child))
        ops.append(op)
    return ops


def _seek(lines: list[str], pattern: list[str], start: int, eof: bool) -> tuple[list[int], str]:
    """Match positions of `pattern` at or after `start`, at the first fuzz level with a hit."""
    n = len(pattern)
    for label, norm in _FUZZ:
        want = [norm(p) for p in pattern]
        hits = [j for j in range(start, len(lines) - n + 1) if [norm(x) for x in lines[j : j + n]] == want]
        if eof and hits:
            hits = [h for h in hits if h == len(lines) - n] or hits[-1:]
        if hits:
            return hits, label
    return [], ""


def _header_at(lines: list[str], hdr: str, start: int, fuzz: set[str]) -> int | None:
    """Line index of an `@@ <context>` header at/after `start`: the whole line (any fuzz
    level), else the first line starting with it (`@@ def main` -> `def main():`). A header
    that is nowhere found anchors nothing (the hunk must then match uniquely)."""
    hits, label = _seek(lines, [hdr], start, False)
    if hits:
        if label != "exact":
            fuzz.add(label)
        return hits[0]
    want = hdr.strip()
    return next((j for j in range(start, len(lines)) if lines[j].strip().startswith(want)), None)


def _apply_hunks(text: str, hunks: list[Hunk], path: str, fuzz: set[str]) -> str:
    trailing = text.endswith("\n")
    lines = text.split("\n")
    if trailing:
        lines.pop()
    cursor = 0
    for i, h in enumerate(hunks, 1):
        start = cursor
        anchored = False
        for hdr in h.headers:
            at = _header_at(lines, hdr, start, fuzz) if hdr else None
            if at is not None:
                start, anchored = at + 1, True
        if not h.old:
            j = start if anchored and not h.eof else len(lines)
            lines[j:j] = h.new
            cursor = j + len(h.new)
            continue
        hits, label = _seek(lines, h.old, start, h.eof)
        if not hits and anchored:  # the context may repeat the header line itself
            hits, label = _seek(lines, h.old, start - 1, h.eof)
        if not hits:
            raise ToolError(f"{path}: hunk {i} not found; include more exact context")
        if len(hits) > 1 and not anchored and not h.eof:
            raise ToolError(f"{path}: hunk {i} matches {len(hits)} places; add context or an @@ header")
        if label != "exact":
            fuzz.add(label)
        j = hits[0]
        lines[j : j + len(h.old)] = h.new
        cursor = j + len(h.new)
    return "\n".join(lines) + ("\n" if trailing else "")


def _guard(ctx: ToolContext, op: Op) -> None:
    for raw in (op.path, op.move_to):
        p = ctx.resolve_path(raw) if raw else None
        if p is not None and _SECRET.search(str(p)) and not str(p).endswith(".env.example"):
            raise ToolError(f"refusing to touch secret file {p}")


def _plan(ctx: ToolContext, ops: list[Op]) -> tuple[dict[Path, str | None], list[str], set[str]]:
    writes: dict[Path, str | None] = {}  # None = delete
    summary: list[str] = []
    fuzz: set[str] = set()
    for op in ops:
        _guard(ctx, op)
        path = ctx.resolve_path(op.path)
        if op.kind == "add":
            if path.exists() and writes.get(path, "") is not None:
                raise ToolError(f"{op.path} already exists (use Update File)")
            writes[path] = "\n".join(op.lines) + "\n"
            summary.append(f"A {op.path}")
            continue
        if not (path.is_file() or writes.get(path)):
            raise ToolError(f"{op.path} does not exist")
        if op.kind == "delete":
            writes[path] = None
            summary.append(f"D {op.path}")
            continue
        current = writes.get(path) or path.read_text(encoding="utf-8")
        new_text = _apply_hunks(current, op.hunks, op.path, fuzz)
        if op.move_to:
            writes[path] = None
            writes[ctx.resolve_path(op.move_to)] = new_text
            summary.append(f"R {op.path} -> {op.move_to}")
        else:
            writes[path] = new_text
            summary.append(f"M {op.path}")
    for path, content in writes.items():  # write-back guard (spec §19.10)
        if content is not None:
            check_write(
                content,
                original=path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None,
            )
    return writes, summary, fuzz


def target_paths(args: dict) -> list[str]:
    """Every path the patch touches (`path` and `move_to`), for the executor's outside-cwd
    gate (`tools/gates.py`); it runs before `run` writes anything."""
    return [p for op in parse(args["patch"]) for p in (op.path, op.move_to) if p]


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    writes, summary, fuzz = _plan(ctx, parse(args["patch"]))
    note = f"\n(matched with {', '.join(sorted(fuzz))})" if fuzz else ""
    if args.get("dry_run"):
        return ToolResult("dry run OK:\n" + "\n".join(summary) + note)
    for path in writes:  # the executor only sees pattern `*`: the sensitive-write floor needs real paths
        if is_sensitive_write(str(path)):
            await ctx.authorize("apply_patch", "edit", str(path))
    for path, content in writes.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ctx.state.read_files[str(path)] = path.stat().st_mtime
    text = "applied:\n" + "\n".join(summary) + note
    touched = [p for p, c in writes.items() if c is not None]
    fmt = await _format(ctx, touched)
    if fmt.formatted:
        text += "\n\nformatted: " + ", ".join(_rel(ctx, p) for p in fmt.formatted)
    if fmt.notes:
        text += "\n(format step: " + "; ".join(fmt.notes) + ")"
    diags = await diagnostics_for(ctx, touched)
    if diags:
        text += "\n\nLSP diagnostics after the patch:\n" + "\n".join(diags)
    meta = {
        "files": len(writes),
        "diagnostics": len(diags),
        "formatted": [_rel(ctx, p) for p in fmt.formatted],
    }
    return ToolResult(text, meta=meta)


def _rel(ctx: ToolContext, path: Path) -> str:
    cwd = ctx.cwd.resolve()
    return str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)


async def _format(ctx: ToolContext, paths: list[Path]) -> FormatReport:
    """Format step (fail-open, opt-out `h5.apply_patch.format`); refreshes read stamps."""
    cfg = h5_of(ctx).apply_patch
    if not cfg.format or not paths:
        return FormatReport()
    from auoreo.core.secrets import child_env
    from auoreo.runtime.cwd import worktree_session

    untrusted = ctx.cwd if worktree_session(ctx) else None  # M7: the worker owns that tree
    try:
        report = await format_files(
            paths, ctx.env or child_env([], None), cfg.format_timeout_s, untrusted=untrusted
        )
    except Exception as exc:  # never fail an applied patch on the formatter
        return FormatReport(notes=[f"skipped ({type(exc).__name__}: {exc})"])
    for path in report.formatted:
        ctx.state.read_files[str(path)] = path.stat().st_mtime
    return report
