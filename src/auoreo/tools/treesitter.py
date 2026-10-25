"""tree-sitter outlines (functions, classes, methods with line ranges) for the `symbols`
tool and `codebase_map`. Needs the optional extra `code` (py-tree-sitter + grammar wheels);
everything is imported lazily so the engine works without it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

MISSING = (
    "tree-sitter is not installed. Install the optional extra `code`: "
    "`uv sync --extra code` (or `pip install 'auoreo[code]'`)."
)

# extension → (grammar module, function returning the language pointer)
GRAMMARS: dict[str, tuple[str, str]] = {
    ".py": ("tree_sitter_python", "language"),
    ".pyi": ("tree_sitter_python", "language"),
    ".js": ("tree_sitter_javascript", "language"),
    ".jsx": ("tree_sitter_javascript", "language"),
    ".mjs": ("tree_sitter_javascript", "language"),
    ".cjs": ("tree_sitter_javascript", "language"),
    ".ts": ("tree_sitter_typescript", "language_typescript"),
    ".mts": ("tree_sitter_typescript", "language_typescript"),
    ".cts": ("tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tree_sitter_typescript", "language_tsx"),
    ".go": ("tree_sitter_go", "language"),
    ".rs": ("tree_sitter_rust", "language"),
}

_JS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "abstract_method_signature": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "variable_declarator": "function",  # only when its value is a function (see _named)
}
# grammar module → node type → symbol kind
KINDS: dict[str, dict[str, str]] = {
    "tree_sitter_python": {"function_definition": "function", "class_definition": "class"},
    "tree_sitter_javascript": _JS,
    "tree_sitter_typescript": _JS,
    "tree_sitter_go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "tree_sitter_rust": {
        "function_item": "function",
        "function_signature_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
    },
}
_CONTAINERS = {"class", "impl", "trait", "interface", "module"}
_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


class TreeSitterMissing(RuntimeError):
    pass


@dataclass
class Symbol:
    kind: str
    name: str
    start: int  # 1-based line
    end: int
    children: list[Symbol] = field(default_factory=list)


def available() -> bool:
    try:
        importlib.import_module("tree_sitter")
    except ImportError:
        return False
    return True


def supported(path: Path) -> bool:
    return path.suffix in GRAMMARS


@cache
def _parser(ext: str) -> Any:
    try:
        ts = importlib.import_module("tree_sitter")
        mod, fn = GRAMMARS[ext]
        grammar = importlib.import_module(mod)
    except ImportError as exc:
        raise TreeSitterMissing(MISSING) from exc
    return ts.Parser(ts.Language(getattr(grammar, fn)()))


def _name(node: Any, kind: str) -> str | None:
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is None or value.type not in _FUNC_VALUES:
            return None
    field_name = "type" if node.type == "impl_item" else "name"
    named = node.child_by_field_name(field_name)
    if named is None or named.text is None:
        return None
    text: str = named.text.decode("utf-8", errors="replace")
    if node.type == "impl_item" and (trait := node.child_by_field_name("trait")) is not None:
        text = f"{trait.text.decode('utf-8', errors='replace')} for {text}"
    return text


def outline_source(source: bytes, ext: str) -> list[Symbol]:
    """Nested symbols of one source text (raises `TreeSitterMissing`, `KeyError` for an
    unsupported extension)."""
    parser = _parser(ext)
    kinds = KINDS[GRAMMARS[ext][0]]
    tree = parser.parse(source)
    out: list[Symbol] = []

    def walk(node: Any, sink: list[Symbol], parent_kind: str | None) -> None:
        for child in node.named_children:
            kind = kinds.get(child.type)
            name = _name(child, kind) if kind else None
            if kind and name:
                if kind == "function" and parent_kind in _CONTAINERS:
                    kind = "method"
                sym = Symbol(kind, name, child.start_point[0] + 1, child.end_point[0] + 1)
                sink.append(sym)
                if kind in _CONTAINERS:  # members, but never locals inside function bodies
                    walk(child, sym.children, kind)
            elif not kind:  # wrappers: export statements, decorators, class bodies, …
                walk(child, sink, parent_kind)

    walk(tree.root_node, out, None)
    return out


def outline_file(path: Path, max_bytes: int = 2_000_000) -> list[Symbol]:
    if path.suffix not in GRAMMARS:
        raise KeyError(path.suffix)
    data = path.read_bytes()[:max_bytes]
    return outline_source(data, path.suffix)


def format_outline(symbols: list[Symbol], indent: int = 0) -> list[str]:
    lines: list[str] = []
    for s in symbols:
        span = f"L{s.start}" if s.start == s.end else f"L{s.start}-{s.end}"
        lines.append(f"{'  ' * indent}{s.kind} {s.name}  {span}")
        lines.extend(format_outline(s.children, indent + 1))
    return lines


def top_level_names(path: Path, limit: int = 25) -> list[str] | None:
    """Top-level symbol names, or None when tree-sitter cannot handle this file."""
    if path.suffix not in GRAMMARS or not available():
        return None
    try:
        syms = outline_file(path, 200_000)
    except (OSError, TreeSitterMissing, ValueError):
        return None
    return [s.name for s in syms][:limit]
