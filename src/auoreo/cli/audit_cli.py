"""H1b: one audit row from a CLI command that has no running server (secrets export/import,
plugin add/verify/enable). Opens `state.db` the same way the server does, writes, closes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from auoreo.core.settings import Settings

log = logging.getLogger(__name__)


async def _record(st: Settings, action: str, decision: str, reason: str, detail: dict[str, Any]) -> None:
    from auoreo.core.audit import AuditLog
    from auoreo.core.db import Database

    st.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(st.db_path, encryption=st.db_encryption)
    await db.init()
    try:
        await AuditLog(db).record(actor="cli", action=action, decision=decision, reason=reason, detail=detail)
    finally:
        await db.close()


def record(
    st: Settings, action: str, detail: dict[str, Any], *, decision: str = "allow", reason: str = ""
) -> bool:
    """Write the row; False (with a log line) when the database cannot be opened."""
    try:
        asyncio.run(_record(st, action, decision, reason, detail))
    except Exception as exc:  # the command's own result must not depend on bookkeeping
        log.warning("audit row %s not written: %s", action, exc)
        return False
    return True
