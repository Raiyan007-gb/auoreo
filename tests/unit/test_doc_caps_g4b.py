"""G4b: document caps (5 MiB fetch, 20 MiB media) and the pandas opt-in for sheet_read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings_tools import MIB, ToolSettings
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext


async def call(services: Services, workdir: Path, name: str, args: dict) -> tuple[str, bool, dict]:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    return out.result.content, out.result.is_error, out.result.meta


def test_default_caps() -> None:
    t = ToolSettings()
    assert t.fetch_max_bytes == 5 * MIB and t.media_max_bytes == 20 * MIB and t.tabular_pandas is False
    assert t.delegate_batch_concurrency == 3 and t.web_search_timeout_s <= 60


@pytest.mark.parametrize(("tool", "name"), [("doc_read", "big.pdf"), ("sheet_read", "big.csv")])
async def test_media_cap(services: Services, workdir: Path, tool: str, name: str) -> None:
    services.settings.tools.media_max_bytes = MIB
    big = workdir / name
    big.write_bytes(b"%PDF-1.4\n" + b"a,b\n" * (MIB // 4 + 10))
    text, err, _ = await call(services, workdir, tool, {"path": name})
    assert err and "larger than the 1 MiB document cap" in text


async def test_sheet_read_pandas_opt_in(services: Services, workdir: Path) -> None:
    (workdir / "t.csv").write_text("name,qty,price\nbolt,3,0.5\nnut,,0.25\nwasher,7,0.1\n")
    text, err, meta = await call(services, workdir, "sheet_read", {"path": "t.csv"})
    assert not err and "engine" not in meta  # builtin by default
    text, err, meta = await call(
        services, workdir, "sheet_read", {"path": "t.csv", "engine": "pandas", "max_rows": 2}
    )
    assert not err and meta == {"rows": 2, "truncated": True, "engine": "pandas"}
    assert "3 columns" in text and "qty: float64" in text and "price: float64" in text
    assert "| name | qty | price |" in text and "| nut |  | 0.25 |" in text
    services.settings.tools.tabular_pandas = True
    _, _, meta = await call(services, workdir, "sheet_read", {"path": "t.csv"})
    assert meta["engine"] == "pandas"
    _, _, meta = await call(services, workdir, "sheet_read", {"path": "t.csv", "engine": "builtin"})
    assert "engine" not in meta


async def test_sheet_read_pandas_xlsx(services: Services, workdir: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "data"
    ws.append(["k", "v"])
    ws.append(["a", 1])
    wb.save(workdir / "w.xlsx")
    text, err, meta = await call(services, workdir, "sheet_read", {"path": "w.xlsx", "engine": "pandas"})
    assert not err and "v: int64" in text and "| a | 1 |" in text
