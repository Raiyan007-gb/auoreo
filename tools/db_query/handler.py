"""db_query: one SELECT against an allow-listed DSN (spec §2.7/§24.2).

The gate is deliberately conservative (no SQL parser): comments stripped, a single
statement, must start with SELECT/WITH, and no write/DDL/PRAGMA keyword anywhere — a
string literal containing such a word is refused too. The query also runs in a
transaction that is always rolled back.
"""

import asyncio
import re
from typing import Any

from auoreo.core.redaction import redact
from auoreo.tools.context import ToolContext, ToolError

CELL_CHARS = 200
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_FORBIDDEN = re.compile(
    r"\b(pragma|attach|detach|insert|update|delete|drop|alter|create|replace\s+into|truncate|"
    r"grant|revoke|vacuum|reindex|into|merge|upsert|copy|call|exec|execute|lock)\b",
    re.I,
)


def select_gate(sql: str) -> tuple[str | None, str]:
    """`(error_or_None, cleaned_sql)`."""
    cleaned = _BLOCK_COMMENT.sub(" ", sql)
    cleaned = _LINE_COMMENT.sub(" ", cleaned).strip()
    if "/*" in cleaned:
        return "unterminated comment", cleaned
    cleaned = cleaned.rstrip().removesuffix(";").rstrip()
    if not cleaned:
        return "empty query", cleaned
    if ";" in cleaned:
        return "only a single statement is allowed (found ';')", cleaned
    if not re.match(r"(?i)(select|with)\b", cleaned):
        return "only SELECT or WITH … SELECT queries are allowed", cleaned
    if m := _FORBIDDEN.search(cleaned):
        return f"keyword '{m.group(1).lower()}' is not allowed in a read-only query", cleaned
    return None, cleaned


def _cell(value: Any) -> str:
    text = "NULL" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if len(text) > CELL_CHARS:
        text = text[:CELL_CHARS] + "…"
    return redact(text)


def _query(url: str, sql: str, max_rows: int) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=False)
    try:
        with engine.connect() as conn:
            try:
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = [tuple(r) for r in result.fetchmany(max_rows + 1)]
            finally:
                conn.rollback()
    finally:
        engine.dispose()
    return cols, rows[:max_rows], len(rows) > max_rows


def _resolve_dsn(ctx: ToolContext, name: str) -> str:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; db_query needs settings.db_query_dsns")
    dsns: dict[str, str] = svc.settings.db_query_dsns
    if name not in dsns:
        known = ", ".join(sorted(dsns)) or "none configured"
        raise ToolError(
            f"unknown dsn_name '{name}' (allow-listed: {known}); add it to settings.db_query_dsns"
        )
    ref = dsns[name]
    if ref.startswith("secret:"):
        value = svc.secrets.get(ref[7:])
        if not value:
            raise ToolError(
                f"secret '{ref[7:]}' for dsn '{name}' is not set (`auoreo secrets set {ref[7:]}`)"
            )
        return str(value)
    return ref


async def run(args: dict, ctx: ToolContext) -> str:
    why, sql = select_gate(args["sql"])
    if why is not None:
        raise ToolError(f"query refused: {why}")
    url = _resolve_dsn(ctx, args["dsn_name"])
    max_rows = int(args.get("max_rows", 200))
    try:
        cols, rows, more = await asyncio.to_thread(_query, url, sql, max_rows)
    except ImportError as exc:
        raise ToolError(f"database driver missing: {exc}") from exc
    except Exception as exc:  # driver/SQL errors are model-visible, never with the DSN
        raise ToolError(
            redact(f"query failed: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
        ) from exc
    if not cols:
        return "(query returned no columns)"
    lines = [
        "| " + " | ".join(_cell(c) for c in cols) + " |",
        "|" + "---|" * len(cols),
        *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows),
    ]
    tail = f"{len(rows)} row(s)" + (f"; capped at max_rows={max_rows}" if more else "")
    return "\n".join(lines) + f"\n\n{tail}"
