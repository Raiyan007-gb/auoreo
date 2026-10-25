"""Structural invariants from the spec: import boundaries, file sizes, no long elif chains."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "auoreo"


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_litellm_is_imported_only_by_the_gateway_module() -> None:
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "litellm" or n.startswith("litellm.") for n in names):
                offenders.append(path.relative_to(SRC))
    assert set(offenders) == {Path("gateway/gateway.py")}


def test_opentelemetry_is_imported_only_by_observability() -> None:
    offenders = set()
    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "opentelemetry" or n.startswith("opentelemetry.") for n in names):
                offenders.add(path.relative_to(SRC))
    assert offenders == {Path("core/observability.py")}


def test_no_god_files() -> None:
    limits = {"api/app.py": 300}
    for path in _py_files():
        rel = str(path.relative_to(SRC))
        lines = path.read_text().count("\n")
        cap = limits.get(rel, 500)
        assert lines <= cap, f"{rel} has {lines} lines (cap {cap})"


def test_no_elif_chains_of_four_or_more() -> None:
    def chain_len(node: ast.If) -> int:
        n = 1
        while len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            n += 1
        return n

    for path in _py_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.If):
                assert chain_len(node) < 4, f"{path.relative_to(SRC)}:{node.lineno} — use dict dispatch"


def test_engine_has_no_hardcoded_tool_bodies() -> None:
    """Tool behaviour lives in tools/<name>/handler.py, never in src/."""
    for path in (SRC / "tools").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
                first = node.args.args[0].arg if node.args.args else ""
                assert first == "self", f"{path}: handler-shaped function in the engine"
