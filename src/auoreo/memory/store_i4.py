"""I4 write/maintenance side of `MemoryStore` (memory.db v3).

- tenants: `provision_tenant` (team_create), `tenant_exists`, `tenant_ids`;
- re-embed (spec §11.1/§24.3): `stale_count`, `stale_batch` (cursor-ordered), `set_embedding`
  and the single-row `reembed_state` (`embed_model, kind, cursor_id, done, total, state`);
- scopes: `set_scope` for promotion/demotion of a fact's scope.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

KINDS: dict[str, tuple[str, str, str]] = {
    "fact": ("facts", "content", "facts_vec"),
    "chunk": ("chunks", "text", "chunks_vec"),
}
SCOPES = ("session", "project", "user", "team")


class I4StoreMixin:
    db: sqlite3.Connection
    tenant: str
    dims: int
    vec_enabled: bool

    def _put_vec(self, table: str, rowid: int, embedding: list[float]) -> None:  # provided by store
        raise NotImplementedError

    # ------------------------------------------------------------ tenants
    def provision_tenant(self, tenant_id: str, kind: str = "team") -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO tenants(id, kind, created_at) VALUES (?,?,?)",
            (tenant_id, kind, time.time()),
        )
        self.db.commit()
        return cur.rowcount > 0

    def tenant_exists(self, tenant_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone() is not None

    def tenant_ids(self) -> list[str]:
        return [str(r[0]) for r in self.db.execute("SELECT id FROM tenants ORDER BY id").fetchall()]

    # ------------------------------------------------------------- scopes
    def set_scope(self, fact_id: int, scope: str) -> bool:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope '{scope}'")
        cur = self.db.execute(
            "UPDATE facts SET scope=?, updated_at=? WHERE id=? AND tenant_id=?",
            (scope, time.time(), fact_id, self.tenant),
        )
        self.db.commit()
        return cur.rowcount > 0

    # ----------------------------------------------------------- re-embed
    def stale_count(self, model: str, kind: str | None = None) -> int:
        """Rows (all tenants) whose stored vector was written by another embedding model."""
        total = 0
        for k, (table, _, _) in KINDS.items():
            if kind in (None, k):
                row = self.db.execute(
                    f"SELECT count(*) FROM {table} WHERE embed_model IS NOT NULL AND embed_model != ?",
                    (model,),
                ).fetchone()
                total += int(row[0])
        return total

    def stale_batch(self, kind: str, model: str, after_id: int, n: int) -> list[tuple[int, str]]:
        table, col, _ = KINDS[kind]
        rows = self.db.execute(
            f"SELECT id, {col} FROM {table} WHERE id > ? AND embed_model IS NOT NULL AND embed_model != ? "
            "ORDER BY id LIMIT ?",
            (after_id, model, n),
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def set_embeddings(self, kind: str, model: str, items: list[tuple[int, list[float]]]) -> None:
        """Write new vectors + the row's `embed_model` in one transaction (dual-write)."""
        table, _, vec = KINDS[kind]
        for rowid, emb in items:
            self._put_vec(vec, rowid, emb)
            self.db.execute(f"UPDATE {table} SET embed_model=? WHERE id=?", (model, rowid))
        self.db.commit()

    def reembed_state(self) -> dict[str, Any] | None:
        cur = self.db.execute(
            "SELECT embed_model, kind, cursor_id, done, total, state, error, updated_at FROM reembed_state"
        )
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None

    def save_reembed_state(self, **fields: Any) -> None:
        state = self.reembed_state() or {
            "embed_model": "",
            "kind": "fact",
            "cursor_id": 0,
            "done": 0,
            "total": 0,
            "state": "idle",
            "error": None,
        }
        state.update(fields, updated_at=time.time())
        self.db.execute(
            "INSERT INTO reembed_state(id, embed_model, kind, cursor_id, done, total, state, error, "
            "updated_at) VALUES (1, :embed_model, :kind, :cursor_id, :done, :total, :state, :error, "
            ":updated_at) "
            "ON CONFLICT(id) DO UPDATE SET embed_model=excluded.embed_model, kind=excluded.kind, "
            "cursor_id=excluded.cursor_id, done=excluded.done, total=excluded.total, state=excluded.state, "
            "error=excluded.error, updated_at=excluded.updated_at",
            state,
        )
        self.db.commit()


__all__ = ["KINDS", "SCOPES", "I4StoreMixin"]
