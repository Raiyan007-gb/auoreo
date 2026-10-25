"""`str_replace` = `edit` under the name some models are trained on. Same logic: the
harness's resolved `edit` handler (overlay-aware), else the bundled `tools/edit/handler.py`."""

import importlib.util
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolResult


def _bundled_edit():
    path = Path(__file__).resolve().parent.parent / "edit" / "handler.py"
    spec = importlib.util.spec_from_file_location("auoreo_tool_edit_for_str_replace", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    edit = ctx.harness.tools.get("edit") if ctx.harness is not None else None
    handler = edit.handler() if edit is not None else _bundled_edit()
    return await handler(args, ctx)
