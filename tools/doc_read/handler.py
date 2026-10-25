import asyncio
import shutil
import tempfile
from pathlib import Path

from auoreo.gateway.types import ImagePart
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part

MAX_BYTES = 100 * 1024 * 1024
MIN_TEXT_CHARS = 20  # fewer than this on a page → treat it as a scan and render it
RENDER_DPI = 100


def parse_pages(spec: str | None, total: int) -> list[int]:
    """1-based page numbers from "1-5,8"; out-of-range pages are dropped."""
    if not spec:
        return list(range(1, total + 1))
    out: list[int] = []
    for chunk in spec.split(","):
        lo, _, hi = chunk.strip().partition("-")
        a, b = int(lo), int(hi or lo)
        if a > b:
            raise ToolError(f"bad page range '{chunk.strip()}' (start after end)")
        out.extend(p for p in range(a, b + 1) if 1 <= p <= total and p not in out)
    return out


def _md_table(rows: list[list[object]]) -> str:
    cells = [
        ["" if c is None else str(c).replace("\n", " ").replace("|", "\\|") for c in r] for r in rows if r
    ]
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
    return "\n".join(lines)


def _reader(path: Path):  # noqa: ANN202 - pypdf imported lazily
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolError("PDF is password-protected")
        return reader
    except PdfReadError as exc:
        raise ToolError(f"cannot parse PDF: {exc}") from exc


def _texts(path: Path, pages: list[int]) -> dict[int, str]:
    reader = _reader(path)
    return {p: (reader.pages[p - 1].extract_text() or "").strip() for p in pages}


def _tables(path: Path, pages: list[int]) -> dict[int, list[str]]:
    import pdfplumber

    out: dict[int, list[str]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for p in pages:
            found = [_md_table(t) for t in pdf.pages[p - 1].extract_tables()]
            out[p] = [t for t in found if t]
    return out


def _count(path: Path) -> int:
    return len(_reader(path).pages)


def _first_jpeg(folder: Path) -> bytes | None:
    files = sorted(folder.glob("page*.jpg"))
    return files[0].read_bytes() if files else None


async def _render(path: Path, page: int, env: dict[str, str]) -> bytes | None:
    exe = shutil.which("pdftoppm")
    if exe is None:
        return None
    with tempfile.TemporaryDirectory(prefix="auoreo-pdf-") as tmp:
        prefix = str(Path(tmp) / "page")
        proc = await asyncio.create_subprocess_exec(
            exe,
            "-f",
            str(page),
            "-l",
            str(page),
            "-jpeg",
            "-r",
            str(RENDER_DPI),
            str(path),
            prefix,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env or None,
        )
        _out, err = await proc.communicate()
        jpg = await asyncio.to_thread(_first_jpeg, Path(tmp))
        if proc.returncode != 0 or jpg is None:
            raise ToolError(f"pdftoppm failed on page {page}: {err.decode(errors='replace')[:300]}")
        return jpg


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    path = ctx.resolve_path(args["path"])
    if not path.is_file():
        raise ToolError(f"file not found: {args['path']}")
    if path.stat().st_size > MAX_BYTES:
        raise ToolError(f"{path} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    with path.open("rb") as fh:
        if not fh.read(1024).lstrip().startswith(b"%PDF-"):
            raise ToolError(f"{path} is not a PDF (no %PDF- header)")
    mode = args.get("mode", "auto")
    total = await asyncio.to_thread(_count, path)
    selected = parse_pages(args.get("pages"), total)
    if not selected:
        raise ToolError(f"no pages selected (the document has {total} pages)")
    cap = int(args.get("max_pages", 20))
    notes: list[str] = []
    if len(selected) > cap:
        notes.append(f"[page cap: read {cap} of {len(selected)} selected pages; ask for more with pages=…]")
        selected = selected[:cap]

    texts = await asyncio.to_thread(_texts, path, selected) if mode != "tables" else {}
    tables = await asyncio.to_thread(_tables, path, selected) if mode != "text" else {}
    images: list[ImagePart] = []
    out = [f"{path.name}: {total} pages; showing {', '.join(map(str, selected))} (mode {mode})", *notes]
    for p in selected:
        out.append(f"\n--- page {p} ---")
        text = texts.get(p, "")
        if mode != "tables":
            if len(text) < MIN_TEXT_CHARS:
                jpg = await _render(path, p, ctx.env)
                if jpg is None:
                    out.append(text or "[no text layer; install poppler (pdftoppm) to render scanned pages]")
                else:
                    img, _meta = await to_part(jpg, f"{path.name} page {p}")
                    images.append(img)
                    note = f"[no text layer: page {p} rendered at {RENDER_DPI} dpi and attached as an image]"
                    out.append(f"{text}\n{note}".strip())
            else:
                out.append(text)
        for i, table in enumerate(tables.get(p, []), start=1):
            out.append(f"\n[table {i}]\n{table}")
        if mode == "tables" and not tables.get(p):
            out.append("[no tables found]")
    return ToolResult(
        "\n".join(out).strip(),
        meta={"pages": selected, "total_pages": total, "images": len(images)},
        images=images,
    )
