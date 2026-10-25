"""tree-sitter outlines (`symbols` tool, `auoreo.tools.treesitter`, codebase_map)."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools import treesitter
from auoreo.tools.context import ToolContext

pytestmark = pytest.mark.skipif(not treesitter.available(), reason="optional extra `code` not installed")

PY = '''import os

def top(a, b):
    def inner():
        pass
    return a

@decorator
class Shape:
    """Doc."""

    def area(self):
        return 0

    async def draw(self):
        pass

if __name__ == "__main__":
    top(1, 2)
'''

TS = """export interface Point { x: number; y: number }
export type Id = string;
export class Circle {
  radius = 1;
  area(): number {
    return 3.14 * this.radius;
  }
}
export const make = (r: number) => new Circle();
const notAFunction = 42;
function helper() {}
enum Color { Red }
"""

TSX = "export function App() {\n  return <div />;\n}\n"
GO = "package m\n\ntype S struct{}\n\nfunc (s S) M() {}\n\nfunc F() {}\n"
RS = "struct S;\n\nimpl S {\n    fn m(&self) {}\n}\n\nfn f() {}\n"


async def call(services: Services, workdir: Path, args: dict) -> tuple[str, bool]:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "symbols", json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    return out.result.content, out.result.is_error


def test_python_outline() -> None:
    lines = treesitter.format_outline(treesitter.outline_source(PY.encode(), ".py"))
    assert lines == [
        "function top  L3-6",
        "class Shape  L9-16",
        "  method area  L12-13",
        "  method draw  L15-16",
    ]


def test_typescript_outline() -> None:
    lines = treesitter.format_outline(treesitter.outline_source(TS.encode(), ".ts"))
    assert lines == [
        "interface Point  L1",
        "type Id  L2",
        "class Circle  L3-8",
        "  method area  L5-7",
        "function make  L9",
        "function helper  L11",
        "enum Color  L12",
    ]
    assert treesitter.format_outline(treesitter.outline_source(TSX.encode(), ".tsx")) == [
        "function App  L1-3"
    ]


def test_go_and_rust_outline() -> None:
    go = treesitter.format_outline(treesitter.outline_source(GO.encode(), ".go"))
    assert go == ["type S  L3", "method M  L5", "function F  L7"]
    rs = treesitter.format_outline(treesitter.outline_source(RS.encode(), ".rs"))
    assert rs == ["struct S  L1", "impl S  L3-5", "  method m  L4", "function f  L7"]


async def test_symbols_tool_file_and_dir(services: Services, workdir: Path) -> None:
    assert services.harness.tools["symbols"].manifest.exposure == "deferred"
    (workdir / "pkg").mkdir()
    (workdir / "pkg" / "shapes.py").write_text(PY)
    (workdir / "web.ts").write_text(TS)
    (workdir / "notes.txt").write_text("hi")
    text, err = await call(services, workdir, {"path": "pkg/shapes.py"})
    assert not err and text.splitlines()[0] == "# pkg/shapes.py" and "  method area  L12-13" in text
    text, err = await call(services, workdir, {"path": "pkg/shapes.py", "depth": 1})
    assert not err and "method" not in text
    text, err = await call(services, workdir, {})
    assert not err and "# pkg/shapes.py" in text and "# web.ts" in text and "notes.txt" not in text
    text, err = await call(services, workdir, {"path": "notes.txt"})
    assert err and "no tree-sitter grammar for '.txt'" in text


async def test_extra_missing_error(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("tree_sitter"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(treesitter.importlib, "import_module", lambda n: fake_import(n))
    treesitter._parser.cache_clear()
    (workdir / "a.py").write_text(PY)
    text, err = await call(services, workdir, {"path": "a.py"})
    assert err and "uv sync --extra code" in text
    assert treesitter.top_level_names(workdir / "a.py") is None
    treesitter._parser.cache_clear()


async def test_codebase_map_uses_tree_sitter(services: Services, workdir: Path) -> None:
    (workdir / "web.ts").write_text(TS)
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "codebase_map", "{}"),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    # the regex fallback only sees `export …` lines; tree-sitter also finds helper/Color
    assert "web.ts: Point, Id, Circle, make, helper, Color" in out.result.content
