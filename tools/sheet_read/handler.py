import asyncio
import csv
import io
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError, ToolResult

MAX_BYTES = 20 * 1024 * 1024  # default media cap
MAX_COLS = 50
MAX_CELL = 200


def _cell(v: object) -> str:
    s = "" if v is None else str(v)
    s = s.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= MAX_CELL else s[: MAX_CELL - 1] + "…"


def _markdown(rows: list[list[object]]) -> str:
    if not rows:
        return "(empty)"
    width = min(MAX_COLS, max(len(r) for r in rows))
    cells = [[_cell(c) for c in r[:width]] + [""] * (width - len(r[:width])) for r in rows]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
    return "\n".join(lines)


def _xlsx(path: Path, sheet: str | None, max_rows: int) -> tuple[str, list[str], list[list[object]], bool]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        if sheet is not None and sheet not in names:
            raise ToolError(f"no sheet '{sheet}'; sheets: {', '.join(names)}")
        ws = wb[sheet] if sheet else wb.active or wb[names[0]]
        rows: list[list[object]] = []
        more = False
        for row in ws.iter_rows(values_only=True):
            if len(rows) > max_rows:
                more = True
                break
            rows.append(list(row))
        while rows and all(v is None for v in rows[-1]):
            rows.pop()
        return str(ws.title), names, rows, more
    finally:
        wb.close()


def _csv(path: Path, max_rows: int) -> tuple[list[list[object]], bool]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    delim = "\t" if path.suffix.lower() == ".tsv" else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    rows: list[list[object]] = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        if len(rows) > max_rows:
            return rows, True
        rows.append(list(row))
    return rows, False


def media_cap(ctx: ToolContext) -> int:
    """Spec §2.8 document/media cap (20 MiB; `settings.tools.media_max_bytes`)."""
    svc = ctx.services.get("services")
    return int(getattr(getattr(getattr(svc, "settings", None), "tools", None), "media_max_bytes", MAX_BYTES))


def _use_pandas(args: dict, ctx: ToolContext) -> bool:
    engine = args.get("engine", "auto")
    if engine != "auto":
        return bool(engine == "pandas")
    svc = ctx.services.get("services")
    return bool(getattr(getattr(getattr(svc, "settings", None), "tools", None), "tabular_pandas", False))


def _pandas(path: Path, sheet: str | None, max_rows: int) -> ToolResult:
    """Opt-in pandas path (extra `tabular`): typed columns, shape and dtypes, then the rows."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ToolError("engine=pandas needs pandas (install the `tabular` extra)") from exc
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        df = pd.read_excel(path, sheet_name=sheet or 0, nrows=max_rows + 1, engine="openpyxl")
    elif suffix in {".csv", ".tsv", ".txt"}:
        if sheet:
            raise ToolError("`sheet` only applies to xlsx workbooks")
        sep = "\t" if suffix == ".tsv" else None
        df = pd.read_csv(path, sep=sep, engine="python", nrows=max_rows + 1, encoding_errors="replace")
    else:
        raise ToolError(f"unsupported spreadsheet type '{suffix}' (use .xlsx, .xlsm, .csv or .tsv)")
    more = len(df) > max_rows
    df = df.head(max_rows)
    rows: list[list[object]] = [[str(c) for c in df.columns]]
    rows += [[None if pd.isna(v) else v for v in r] for r in df.itertuples(index=False, name=None)]
    dtypes = ", ".join(f"{c}: {t}" for c, t in list(df.dtypes.astype(str).items())[:MAX_COLS])
    note = f"\n[truncated: showing the first {max_rows} data rows]" if more else ""
    head = f"{path.name} (pandas) — {len(df)} data rows x {len(df.columns)} columns\ndtypes: {dtypes}"
    return ToolResult(
        f"{head}\n\n{_markdown(rows)}{note}", meta={"rows": len(df), "truncated": more, "engine": "pandas"}
    )


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"file not found: {args['path']}")
    if path.stat().st_size > (cap_bytes := media_cap(ctx)):
        raise ToolError(f"{path} is larger than the {cap_bytes / (1024 * 1024):g} MiB document cap")
    max_rows = int(args.get("max_rows", 200))
    suffix = path.suffix.lower()
    header = ""
    if _use_pandas(args, ctx):
        return await asyncio.to_thread(_pandas, path, args.get("sheet"), max_rows)
    if suffix in {".xlsx", ".xlsm"}:
        with path.open("rb") as fh:
            if fh.read(4) != b"PK\x03\x04":
                raise ToolError(f"{path} is not an xlsx workbook (not a zip container)")
        title, names, rows, more = await asyncio.to_thread(_xlsx, path, args.get("sheet"), max_rows)
        header = f"{path.name} — sheet '{title}' (sheets: {', '.join(names)})"
    elif suffix in {".csv", ".tsv", ".txt"}:
        if args.get("sheet"):
            raise ToolError("`sheet` only applies to xlsx workbooks")
        rows, more = await asyncio.to_thread(_csv, path, max_rows)
        header = f"{path.name}"
    else:
        raise ToolError(f"unsupported spreadsheet type '{suffix}' (use .xlsx, .xlsm, .csv or .tsv)")
    shown = rows[: max_rows + 1]  # header row + max_rows data rows
    note = (
        f"\n[truncated: showing the first {max_rows} data rows; raise max_rows to see more]" if more else ""
    )
    wide = rows and max(len(r) for r in rows) > MAX_COLS
    if wide:
        note += f"\n[only the first {MAX_COLS} columns are shown]"
    return ToolResult(
        f"{header}, {len(shown) - 1 if shown else 0} data rows\n\n{_markdown(shown)}{note}",
        meta={"rows": len(shown), "truncated": more},
    )
