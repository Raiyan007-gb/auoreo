"""Read-only LSP queries (9 ops) through `auoreo.tools.lsp_client`.

Output is compact `path:line:col  snippet` lines (1-based, code-point columns), relative to
the working directory, minus git-ignored paths (checked in batches of 50), capped; the
executor's pruner spills anything larger.
"""

import asyncio
from pathlib import Path
from typing import Any

from auoreo.core.secrets import child_env
from auoreo.tools.context import ToolContext, ToolError
from auoreo.tools.lsp_client import (
    EXT_LANG,
    LspClient,
    LspError,
    column_from_utf16,
    find_root,
    manager_for,
    path_to_uri,
    uri_to_path,
    utf16_col,
)

MAX_LINES = 200
GIT_BATCH = 50
POSITIONAL = {"definition", "references", "hover", "implementation", "type_definition", "call_hierarchy"}
CAPABILITY = {
    "definition": "definitionProvider",
    "references": "referencesProvider",
    "hover": "hoverProvider",
    "document_symbols": "documentSymbolProvider",
    "workspace_symbols": "workspaceSymbolProvider",
    "implementation": "implementationProvider",
    "type_definition": "typeDefinitionProvider",
    "call_hierarchy": "callHierarchyProvider",
}
LOCATION_METHOD = {
    "definition": "textDocument/definition",
    "implementation": "textDocument/implementation",
    "type_definition": "textDocument/typeDefinition",
}
_KINDS = (
    "file module namespace package class method property field constructor enum interface function "
    "variable constant string number boolean array object key null enummember struct event operator "
    "typeparameter"
)
SYMBOL_KIND = dict(enumerate(_KINDS.split(), start=1))
SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}
_PROJECT_HINTS = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "tsconfig.json": "typescript",
    "package.json": "javascript",
    "Cargo.toml": "rust",
    "go.mod": "go",
}


class Fmt:
    """Path/line rendering with a per-call file cache."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._lines: dict[Path, list[str] | None] = {}

    def lines(self, path: Path) -> list[str] | None:
        if path not in self._lines:
            try:
                self._lines[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._lines[path] = None
        return self._lines[path]

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.cwd)) if path.is_relative_to(self.cwd) else str(path)

    def loc(self, uri: str, pos: dict[str, Any] | None, note: str = "") -> tuple[Path, str]:
        path = uri_to_path(uri).resolve()
        if not pos:
            return path, f"{self.rel(path)}  {note}".rstrip()
        line0 = int(pos.get("line", 0))
        src = self.lines(path) or []
        text = src[line0] if 0 <= line0 < len(src) else ""
        units = int(pos.get("character", 0))
        col = column_from_utf16(text, units) if text else units + 1  # unreadable: assume ASCII
        snippet = " ".join(text.split())[:160]
        tail = f"  {snippet}" if snippet else ""
        return path, f"{self.rel(path)}:{line0 + 1}:{col}{tail}{f'  ({note})' if note else ''}"


async def _git_ignored(cwd: Path, paths: list[Path]) -> set[Path]:
    inside = sorted({p for p in paths if p.is_relative_to(cwd)})
    if not inside:
        return set()
    probe = await asyncio.create_subprocess_exec(
        "git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await probe.communicate()
    if probe.returncode != 0 or out.strip() != b"true":
        return set()
    ignored: set[Path] = set()
    for i in range(0, len(inside), GIT_BATCH):
        batch = [str(p.relative_to(cwd)) for p in inside[i : i + GIT_BATCH]]
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(cwd), "check-ignore", "--", *batch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        out, _ = await proc.communicate()
        ignored |= {(cwd / ln).resolve() for ln in out.decode(errors="replace").splitlines() if ln}
    return ignored


async def _finish(
    fmt: Fmt, rows: list[tuple[Path, str]], empty: str, header: str = "", *, filter_ignored: bool = True
) -> str:
    """Dedupe, drop git-ignored paths (multi-result ops), cap at MAX_LINES."""
    ignored = await _git_ignored(fmt.cwd, [p for p, _ in rows]) if filter_ignored else set()
    seen: set[str] = set()
    lines = []
    for path, text in rows:
        if path in ignored or text in seen:
            continue
        seen.add(text)
        lines.append(text)
    hidden = len(rows) - len(lines)
    if not lines:
        return f"{header}{empty}" + (f" ({hidden} git-ignored hidden)" if hidden else "")
    more = f"\n… {len(lines) - MAX_LINES} more" if len(lines) > MAX_LINES else ""
    return header + "\n".join(lines[:MAX_LINES]) + more


def _locations(result: Any) -> list[tuple[str, dict[str, Any] | None]]:
    items = result if isinstance(result, list) else [result] if result else []
    out = []
    for it in items:
        if "targetUri" in it:  # LocationLink
            rng = it.get("targetSelectionRange") or it.get("targetRange") or {}
            out.append((it["targetUri"], rng.get("start")))
        elif "uri" in it:
            out.append((it["uri"], (it.get("range") or {}).get("start")))
    return out


def _hover_text(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        return "\n\n".join(_hover_text(c) for c in contents)
    if isinstance(contents, dict):
        value = str(contents.get("value", ""))
        lang = contents.get("language")
        return f"```{lang}\n{value}\n```" if lang else value
    return ""


def _doc_symbols(fmt: Fmt, uri: str, items: list[Any], depth: int = 0) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    for s in items or []:
        kind = SYMBOL_KIND.get(int(s.get("kind", 0)), "symbol")
        if "selectionRange" in s:  # DocumentSymbol (hierarchical)
            start, end = s["selectionRange"]["start"], s["range"]["end"]
            path, text = fmt.loc(uri, start)
            loc = text.split("  ", 1)[0]
            rows.append((path, f"{'  ' * depth}{kind} {s.get('name')}  {loc}-{end['line'] + 1}"))
            rows += _doc_symbols(fmt, uri, s.get("children") or [], depth + 1)
        else:  # SymbolInformation
            loc = s.get("location") or {}
            path, text = fmt.loc(loc.get("uri", uri), (loc.get("range") or {}).get("start"))
            rows.append((path, f"{kind} {s.get('name')}  {text.split('  ', 1)[0]}"))
    return rows


async def _call_hierarchy(client: LspClient, fmt: Fmt, pos: dict[str, Any], direction: str) -> str:
    items = await client.request("textDocument/prepareCallHierarchy", pos) or []
    if not items:
        return "no call hierarchy item at this position"
    item = items[0]
    _, head = fmt.loc(item["uri"], item["selectionRange"]["start"])
    method = "callHierarchy/incomingCalls" if direction == "incoming" else "callHierarchy/outgoingCalls"
    calls = await client.request(method, {"item": item}) or []
    rows = []
    for call in calls:
        other = call.get("from" if direction == "incoming" else "to") or {}
        if direction == "incoming":  # call sites inside the caller
            for rng in call.get("fromRanges") or [other.get("selectionRange", {})]:
                rows.append(fmt.loc(other.get("uri", ""), rng.get("start"), f"in {other.get('name')}"))
        else:  # callee definitions
            rows.append(fmt.loc(other.get("uri", ""), other.get("selectionRange", {}).get("start")))
    arrow = "callers of" if direction == "incoming" else "callees of"
    return await _finish(fmt, rows, f"no {direction} calls", f"{arrow} {item.get('name')} — {head}\n")


async def _diagnostics(client: LspClient, fmt: Fmt, uri: str) -> str:
    if client.capabilities.get("diagnosticProvider"):
        report = await client.request("textDocument/diagnostic", {"textDocument": {"uri": uri}}) or {}
        items = report.get("items") or []
    else:
        items = await client.wait_diagnostics(uri, client.timeout_s)
    rows = []
    for d in items:
        sev = SEVERITY.get(int(d.get("severity") or 1), "error")
        code = f" [{d['code']}]" if d.get("code") is not None else ""
        msg = " ".join(str(d.get("message", "")).split())[:300]
        path, text = fmt.loc(uri, (d.get("range") or {}).get("start"))
        rows.append((path, f"{text.split('  ', 1)[0]}  {sev}: {msg}{code}"))
    return await _finish(fmt, rows, "no diagnostics")


def _language(path: Path | None, cwd: Path) -> str:
    if path is not None:
        if path.suffix not in EXT_LANG:
            known = " ".join(sorted(EXT_LANG))
            raise ToolError(f"no language server for '{path.suffix or path.name}' (supported: {known})")
        return EXT_LANG[path.suffix][0]
    for marker, lang in _PROJECT_HINTS.items():
        if (cwd / marker).exists():
            return lang
    raise ToolError("cannot tell the project language; pass a source file as path")


async def run(args: dict, ctx: ToolContext) -> str:
    op = args["op"]
    cwd = ctx.cwd.resolve()
    path = ctx.resolve_path(args["path"]) if args.get("path") else None
    if op != "workspace_symbols" and path is None:
        raise ToolError(f"{op} needs path")
    if path is not None and not path.is_file():
        raise ToolError(f"not a file: {path}")
    if op in POSITIONAL and (not args.get("line") or not args.get("column")):
        raise ToolError(f"{op} needs line and column (1-based) on the symbol")
    if op == "workspace_symbols" and not args.get("query"):
        raise ToolError("workspace_symbols needs query")
    language = _language(path, cwd)
    stop = cwd if path is None or path.is_relative_to(cwd) else Path(path.anchor)
    root = find_root(path or cwd, language, stop)
    if root is None:
        raise ToolError(f"{language}: this file belongs to a Deno project (deno.json); no server for it")
    env = ctx.env or child_env([], None)
    try:
        client = await manager_for(ctx.services.get("services")).client_for(
            ctx.session_id, language, root, env
        )
        return await _run_op(op, args, client, Fmt(cwd), path)
    except LspError as exc:
        raise ToolError(f"lsp {op}: {exc}") from exc


async def _op_locations(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    result = await client.request(LOCATION_METHOD[op], pos)
    rows = [fmt.loc(u, p) for u, p in _locations(result)]
    # a definition inside an ignored dir (e.g. .venv site-packages) is still the answer
    return await _finish(fmt, rows, f"no {op.replace('_', ' ')} found", filter_ignored=op == "implementation")


async def _op_references(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    result = await client.request("textDocument/references", {**pos, "context": {"includeDeclaration": True}})
    return await _finish(fmt, [fmt.loc(u, p) for u, p in _locations(result)], "no references found")


async def _op_hover(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    result = await client.request("textDocument/hover", pos) or {}
    text = _hover_text(result.get("contents")).strip()
    return text[:4000] if text else "no hover information"


async def _op_doc_symbols(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    result = await client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}) or []
    return await _finish(fmt, _doc_symbols(fmt, uri, result), "no symbols")


async def _op_ws_symbols(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    result = await client.request("workspace/symbol", {"query": args["query"]}) or []
    return await _finish(fmt, _doc_symbols(fmt, path_to_uri(client.root), result), "no symbols matched")


async def _op_diagnostics(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    return await _diagnostics(client, fmt, uri)


async def _op_calls(op: str, args: dict, client: LspClient, fmt: Fmt, uri: str, pos: dict) -> str:
    return await _call_hierarchy(client, fmt, pos, args.get("direction", "incoming"))


OPS = {
    "definition": _op_locations,
    "implementation": _op_locations,
    "type_definition": _op_locations,
    "references": _op_references,
    "hover": _op_hover,
    "document_symbols": _op_doc_symbols,
    "workspace_symbols": _op_ws_symbols,
    "diagnostics": _op_diagnostics,
    "call_hierarchy": _op_calls,
}


async def _run_op(op: str, args: dict, client: LspClient, fmt: Fmt, path: Path | None) -> str:
    if op in CAPABILITY and not client.capabilities.get(CAPABILITY[op]):
        raise ToolError(f"{client.argv[0]} does not support {op}; fall back to grep/read")
    uri = await client.open(path, EXT_LANG[path.suffix][1]) if path is not None else ""
    pos: dict[str, Any] = {}
    if op in POSITIONAL:
        assert path is not None
        src = fmt.lines(path) or []
        line = int(args["line"])
        if line > max(len(src), 1):
            raise ToolError(f"line {line} is past the end of {fmt.rel(path)} ({len(src)} lines)")
        char = utf16_col(src[line - 1] if src else "", int(args["column"]))
        pos = {"textDocument": {"uri": uri}, "position": {"line": line - 1, "character": char}}
    return await OPS[op](op, args, client, fmt, uri, pos)
