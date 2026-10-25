"""Append-only audit log with a SHA-256 hash chain for tamper evidence (spec §19.13)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from sqlalchemy import select

from auoreo.core import observability as obs
from auoreo.core.db import AuditRow, Database, utcnow
from auoreo.core.redaction import redact_obj

GENESIS = hashlib.sha256(b"auoreo-audit-v1").hexdigest()


def _row_hash(
    prev: str,
    seq: int,
    ts: str,
    session_id: str | None,
    actor: str,
    action: str,
    decision: str,
    reason: str,
    detail: dict[str, Any],
) -> str:
    canon = json.dumps(
        [seq, ts, session_id, actor, action, decision, reason, detail, prev],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canon.encode()).hexdigest()


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        actor: str,
        action: str,
        session_id: str | None = None,
        decision: str = "",
        reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        # W1-E: the chain covers the STORED value (ciphertext in `columns` mode) → verify() is keyless
        stored = self.db.cipher.encrypt(redact_obj(detail or {}))
        async with self._lock, self.db.session() as s:
            last = (await s.execute(select(AuditRow).order_by(AuditRow.seq.desc()).limit(1))).scalar()
            prev = last.row_hash if last else GENESIS
            seq = (last.seq if last else 0) + 1
            ts = utcnow()
            row = AuditRow(
                seq=seq,
                ts=ts,
                session_id=session_id,
                actor=actor,
                action=action,
                decision=decision,
                reason=reason,
                detail=stored,
                prev_hash=prev,
                row_hash=_row_hash(
                    prev, seq, ts.isoformat(), session_id, actor, action, decision, reason, stored
                ),
            )
            s.add(row)
            await s.commit()
        obs.count("audit_writes_total")  # --- W0-D

    def plain_detail(self, row: AuditRow) -> dict[str, Any]:
        """The one read path for `audit.detail`: decrypt (if enveloped), then redact."""
        out: dict[str, Any] = redact_obj(self.db.cipher.decrypt(row.detail or {}))
        return out

    async def verify(self) -> int | None:
        """Recompute the chain. Returns the first broken seq, or None if intact."""
        prev = GENESIS
        async with self.db.session() as s:
            for row in (await s.execute(select(AuditRow).order_by(AuditRow.seq))).scalars():
                expect = _row_hash(
                    prev,
                    row.seq,
                    row.ts.isoformat(),
                    row.session_id,
                    row.actor,
                    row.action,
                    row.decision,
                    row.reason,
                    row.detail,
                )
                if row.prev_hash != prev or row.row_hash != expect:
                    return row.seq
                prev = row.row_hash
        return None
