"""M7 W4: what `auoreo worktree gc` (and `doctor --fix`) must keep, read from the task store.

- protected: worktrees of claimed kanban tasks (`worktree_spawn.protected_paths`) and of live
  sessions: a team member still `queued`/`running`, or a session whose status is `running`,
  matched to its worktree through the `worktree.created` event (`path`) or the member's `cwd`;
- activity: per worktree, the time of the newest event of any session that ran there. gc
  takes the newest of that, the HEAD commit and the folder's mtime as "last touched", so a
  child that works for hours without committing never looks merged or stale.

Only used with the M7 flags on; gc without them is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from auoreo.core.db import EventRow, SessionRow, TeamMemberRow
from auoreo.teams.worktree_spawn import protected_paths

if TYPE_CHECKING:
    from auoreo.core.db import Database

LIVE_MEMBER = ("queued", "running")


@dataclass
class GcGuard:
    protected: set[Path] = field(default_factory=set)
    activity: dict[Path, float] = field(default_factory=dict)


def _epoch(value: Any) -> float:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return 0.0
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp()


async def guard(db: Database) -> GcGuard:
    sessions: dict[str, Path] = {}
    out = GcGuard(protected=await protected_paths(db))
    async with db.session() as s:
        created = select(EventRow).where(EventRow.type == "worktree.created")
        for row in (await s.execute(created)).scalars():
            path = db.cipher.decrypt(row.data).get("path")
            if isinstance(path, str) and path:
                sessions[row.aggregate_id] = Path(path)
        members = select(TeamMemberRow).where(TeamMemberRow.cwd.is_not(None))
        for m in (await s.execute(members)).scalars():
            sessions.setdefault(m.session_id, Path(str(m.cwd)))
            if m.status in LIVE_MEMBER:
                out.protected.add(Path(str(m.cwd)))
        if not sessions:
            return out
        ids = list(sessions)
        running = select(SessionRow.id).where(SessionRow.id.in_(ids), SessionRow.status == "running")
        out.protected |= {sessions[sid] for sid in (await s.execute(running)).scalars()}
        newest = (
            select(EventRow.aggregate_id, func.max(EventRow.created_at))
            .where(EventRow.aggregate_id.in_(ids))
            .group_by(EventRow.aggregate_id)
        )
        for sid, when in (await s.execute(newest)).all():
            path = sessions[sid].resolve()
            out.activity[path] = max(out.activity.get(path, 0.0), _epoch(when))
    return out


async def guard_from_settings(st: Any, warn: Any = None) -> GcGuard:
    """`guard` over the configured state database; empty (with `warn(note)`) when it cannot be
    read, so gc still works without the task store."""
    from auoreo.core.db import Database

    if not st.db_path.exists():
        return GcGuard()
    try:
        db = Database(st.db_path, encryption=st.db_encryption)
        await db.init()
        try:
            return await guard(db)
        finally:
            await db.close()
    except Exception as exc:
        if warn is not None:
            warn(f"task store not readable ({exc})")
        return GcGuard()
