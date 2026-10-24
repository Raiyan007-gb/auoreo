"""Codex-style patch format, parsed with a Lark grammar (grammar.lark) and applied
transactionally: every operation is validated before any file is written."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from lark import Lark, Token, Tree
from lark.exceptions import LarkError

from auoreo.tools.context import ToolContext, ToolError, ToolResult

_PARSER = Lark((Path(__file__).parent / "grammar.lark").read_text(), parser="lalr")
_SECRET = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.db)$")


@dataclass
class Hunk:
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)


@dataclass
class Op:
    kind: str
    path: str
    move_to: str | None = None
    lines: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)


def _text(node: Tree | Token) -> str:
    return str(node) if isinstance(node, Token) else ""


def parse(patch: str) -> list[Op]:
    if not patch.endswith("\n"):
        patch += "\n"
    try:
        tree = _PARSER.parse(patch)
    except LarkError as exc:
        raise ToolError(f"patch does not match the format: {str(exc).splitlines()[0]}") from exc
    ops: list[Op] = []
    for op_tree in tree.find_data("op"):
        node = op_tree.children[0]
        path = next(_text(c) for c in node.children if isinstance(c, Token) and c.type == "PATH").strip()
        op = Op(node.data, path)
        if node.data == "add":
            for line in node.find_data("add_line"):
                op.lines.append(next((str(c) for c in line.children if c.type == "LINE"), ""))
        elif node.data == "update":
            for child in node.children:
                if isinstance(child, Tree) and child.data == "move":
                    op.move_to = next(str(c) for c in child.children if c.type == "PATH").strip()
                elif isinstance(child, Tree) and child.data == "hunk":
                    hunk = Hunk()
                    for hl in child.find_data("hunk_line"):
                        tok = hl.children[0]
                        body = str(tok)[1:]
                        if tok.type in {"CONTEXT", "REMOVE"}:
                            hunk.old.append(body)
                        if tok.type in {"CONTEXT", "ADDED"}:
                            hunk.new.append(body)
                    op.hunks.append(hunk)
        ops.append(op)
    return ops


def _apply_hunks(text: str, hunks: list[Hunk], path: str) -> str:
    lines = text.split("\n")
    for i, h in enumerate(hunks, 1):
        if not h.old:
            raise ToolError(f"{path}: hunk {i} has no context or removed lines")
        n = len(h.old)
        hits = [j for j in range(len(lines) - n + 1) if lines[j : j + n] == h.old]
        if len(hits) != 1:
            what = "not found" if not hits else f"matches {len(hits)} places"
            raise ToolError(f"{path}: hunk {i} {what}; include more exact context")
        j = hits[0]
        lines[j : j + n] = h.new
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    ops = parse(args["patch"])
    writes: dict[Path, str | None] = {}  # None = delete
    summary: list[str] = []
    for op in ops:
        path = ctx.resolve_path(op.path)
        for p in (path, ctx.resolve_path(op.move_to) if op.move_to else None):
            if p is not None and _SECRET.search(str(p)) and not str(p).endswith(".env.example"):
                raise ToolError(f"refusing to touch secret file {p}")
        if op.kind == "add":
            if path.exists():
                raise ToolError(f"{op.path} already exists (use Update File)")
            writes[path] = "\n".join(op.lines) + "\n"
            summary.append(f"A {op.path}")
        elif op.kind == "delete":
            if not path.is_file():
                raise ToolError(f"{op.path} does not exist")
            writes[path] = None
            summary.append(f"D {op.path}")
        else:
            if not path.is_file():
                raise ToolError(f"{op.path} does not exist")
            new_text = _apply_hunks(writes.get(path) or path.read_text(encoding="utf-8"), op.hunks, op.path)
            if op.move_to:
                dest = ctx.resolve_path(op.move_to)
                writes[path] = None
                writes[dest] = new_text
                summary.append(f"R {op.path} -> {op.move_to}")
            else:
                writes[path] = new_text
                summary.append(f"M {op.path}")
    if args.get("dry_run"):
        return ToolResult("dry run OK:\n" + "\n".join(summary))
    for path, content in writes.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        ctx.state.read_files[str(path)] = path.stat().st_mtime
    return ToolResult("applied:\n" + "\n".join(summary), meta={"files": len(writes)})
