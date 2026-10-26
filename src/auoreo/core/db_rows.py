"""I5: `auoreo db inspect <sessions|events|audit> [--session X] [--tail N]` (spec §20.3).

Read-only (`mode=ro` SQLite URI): the last N rows of one table, oldest first, optionally for
one session. JSON payloads are redacted and truncated; a column that does not parse as JSON
(an encrypted column, `db.encryption`) is shown as `[encrypted]`, never decrypted here.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from auoreo.core.redaction import redact
from auoreo.core.settings import Settings

TARGETS: dict[str, tuple[str, str, str, str]] = {
    # target: (table, columns, session column, order column)
    "sessions": (
        "sessions",
        "id, title, agent, workflow, model, status, archived, updated_at",
        "id",
        "updated_at",
    ),
    "events": ("events", "seq, aggregate_id, aggregate_seq, type, created_at, data", "aggregate_id", "seq"),
    "audit": ("audit", "seq, ts, session_id, actor, action, decision, reason, detail", "session_id", "seq"),
}
JSON_COLUMNS = frozenset({"data", "detail"})
MAX_JSON_CHARS = 2000


class DbInspectError(ValueError):
    """Unknown target, missing or encrypted database."""


def _cell(name: str, value: Any) -> Any:
    if name not in JSON_COLUMNS or value is None:
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return "[encrypted]"
    text = redact(json.dumps(parsed, ensure_ascii=False, default=str))
    if len(text) > MAX_JSON_CHARS:
        return text[:MAX_JSON_CHARS] + "…"
    try:
        return json.loads(text)
    except ValueError:
        return text


def inspect_rows(
    st: Settings, target: str, session: str | None = None, tail: int = 20
) -> list[dict[str, Any]]:
    from auoreo.core import sqlcipher

    if target not in TARGETS:
        raise DbInspectError(f"target must be one of {', '.join(TARGETS)}")
    if not st.db_path.is_file():
        raise DbInspectError(f"{st.db_path} does not exist yet")
    if sqlcipher.is_encrypted_file(st.db_path):
        raise DbInspectError("state.db is SQLCipher-encrypted: inspect rows inside the server (API)")
    table, cols, sess_col, order = TARGETS[target]
    where, params = (f" WHERE {sess_col} = ?", [session]) if session else ("", [])
    sql = f"SELECT {cols} FROM {table}{where} ORDER BY {order} DESC LIMIT ?"
    con = sqlite3.connect(f"file:{st.db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(sql, [*params, max(1, min(tail, 10_000))])
        names = [d[0] for d in cur.description]
        rows = [{n: _cell(n, v) for n, v in zip(names, r, strict=True)} for r in cur.fetchall()]
    finally:
        con.close()
    return list(reversed(rows))
