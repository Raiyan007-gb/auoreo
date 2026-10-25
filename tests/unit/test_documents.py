"""M4a: doc_read (PDF text / tables / page cap / scanned-page render) and sheet_read."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook

from auoreo.core.services import Services
from tests import media
from tests.unit.test_vision import call, ctx_for

needs_pdftoppm = pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler not installed")


async def test_doc_read_extracts_text(services: Services, workdir: Path) -> None:
    (workdir / "doc.pdf").write_bytes(media.text_pdf(["Hello from the first page", "Second page text here"]))
    res = await call(services, ctx_for(services, workdir), "doc_read", {"path": "doc.pdf", "mode": "text"})
    assert not res.is_error, res.content
    assert "doc.pdf: 2 pages" in res.content
    assert "--- page 1 ---\nHello from the first page" in res.content
    assert "--- page 2 ---\nSecond page text here" in res.content
    assert res.images == [] and res.meta["total_pages"] == 2


async def test_doc_read_page_selection_and_cap(services: Services, workdir: Path) -> None:
    pages = [f"This is page number {i} of the report" for i in range(1, 8)]
    (workdir / "long.pdf").write_bytes(media.text_pdf(pages))
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "doc_read", {"path": "long.pdf", "pages": "2-3,6", "mode": "text"})
    assert (
        res.meta["pages"] == [2, 3, 6]
        and "page number 6" in res.content
        and "page number 1 " not in res.content
    )
    res = await call(services, ctx, "doc_read", {"path": "long.pdf", "max_pages": 3, "mode": "text"})
    assert res.meta["pages"] == [1, 2, 3]
    assert "[page cap: read 3 of 7 selected pages" in res.content and "page number 4" not in res.content
    res = await call(services, ctx, "doc_read", {"path": "long.pdf", "pages": "5-2"})
    assert res.is_error and "start after end" in res.content
    res = await call(services, ctx, "doc_read", {"path": "long.pdf", "pages": "abc"})
    assert res.is_error and "invalid arguments" in res.content


async def test_doc_read_tables(services: Services, workdir: Path) -> None:
    (workdir / "t.pdf").write_bytes(media.table_pdf())
    res = await call(services, ctx_for(services, workdir), "doc_read", {"path": "t.pdf", "mode": "tables"})
    assert not res.is_error, res.content
    assert "| Name | Qty | Price |" in res.content and "| Apple | 3 | 1.20 |" in res.content
    (workdir / "plain.pdf").write_bytes(media.text_pdf(["Just some prose without any table"]))
    res = await call(
        services, ctx_for(services, workdir), "doc_read", {"path": "plain.pdf", "mode": "tables"}
    )
    assert "[no tables found]" in res.content


async def test_doc_read_rejects_non_pdf(services: Services, workdir: Path) -> None:
    (workdir / "fake.pdf").write_bytes(b"PK\x03\x04 not a pdf")
    res = await call(services, ctx_for(services, workdir), "doc_read", {"path": "fake.pdf"})
    assert res.is_error and "no %PDF- header" in res.content


@needs_pdftoppm
async def test_doc_read_renders_scanned_pages_as_images(services: Services, workdir: Path) -> None:
    media.image_pdf(workdir / "scan.pdf")
    res = await call(services, ctx_for(services, workdir), "doc_read", {"path": "scan.pdf"})
    assert not res.is_error, res.content
    assert "[no text layer: page 1 rendered at 100 dpi and attached as an image]" in res.content
    assert len(res.images) == 1 and res.images[0].media_type == "image/jpeg"
    assert "scan.pdf page 1" in res.images[0].note


async def test_doc_read_without_pdftoppm_says_so(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media.image_pdf(workdir / "scan.pdf")
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "pdftoppm" else real(name))
    res = await call(services, ctx_for(services, workdir), "doc_read", {"path": "scan.pdf"})
    assert not res.is_error and res.images == [] and "install poppler" in res.content


async def test_sheet_read_xlsx(services: Services, workdir: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Sales"
    ws.append(["Region", "Units", "Note"])
    for i in range(1, 6):
        ws.append([f"R{i}", i * 10, "a|b" if i == 1 else None])
    other = wb.create_sheet("Other")
    other.append(["x", "y"])
    other.append([1, 2])
    wb.save(workdir / "book.xlsx")
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "sheet_read", {"path": "book.xlsx"})
    assert not res.is_error, res.content
    assert "sheet 'Sales' (sheets: Sales, Other), 5 data rows" in res.content
    assert "| Region | Units | Note |" in res.content and "| R1 | 10 | a\\|b |" in res.content
    res = await call(services, ctx, "sheet_read", {"path": "book.xlsx", "sheet": "Other"})
    assert "| x | y |\n| --- | --- |\n| 1 | 2 |" in res.content
    res = await call(services, ctx, "sheet_read", {"path": "book.xlsx", "max_rows": 2})
    assert "2 data rows" in res.content and "[truncated: showing the first 2 data rows" in res.content
    res = await call(services, ctx, "sheet_read", {"path": "book.xlsx", "sheet": "Nope"})
    assert res.is_error and "no sheet 'Nope'" in res.content


async def test_sheet_read_csv_and_tsv(services: Services, workdir: Path) -> None:
    (workdir / "data.csv").write_text("name,score\nada,99\nbob,7\n", encoding="utf-8")
    (workdir / "data.tsv").write_text("k\tv\na\t1\n", encoding="utf-8")
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "sheet_read", {"path": "data.csv"})
    assert not res.is_error and "| name | score |\n| --- | --- |\n| ada | 99 |\n| bob | 7 |" in res.content
    res = await call(services, ctx, "sheet_read", {"path": "data.tsv"})
    assert "| k | v |" in res.content and "| a | 1 |" in res.content
    (workdir / "x.ods").write_bytes(b"")
    res = await call(services, ctx, "sheet_read", {"path": "x.ods"})
    assert res.is_error and "unsupported spreadsheet type" in res.content
