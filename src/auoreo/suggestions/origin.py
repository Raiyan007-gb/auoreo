"""Where a suggestion came from (M7 W3 wave 2, `docs/designs/m7-agents-bots.md`).

`origin_of(services, session_id)` classifies the session whose agent proposed a suggestion:

- `cron`: a job or webhook run (a `deliveries` row of kind job/webhook names the session);
  source `job:<name>` / `webhook:<route>`;
- `platform`: a messaging-connector chat (the plugin's `sessions.json` maps a chat to it, the
  scan `core/approvals.py` uses for unattended approvals); source `plugin:<name>`;
- `worker`: a kanban worker (a task row names the session, a dispatcher-pinned session, or a
  worker process started with `AUOREO_KANBAN_BOARD`); source `worker:<board>`;
- `attended`: anything else (the web UI, the CLI, no session at all); source `session`.

Only `attended` sessions have a human reading the output as it runs. With
`m7.agents.strict_origins` on, the engine treats the other three as unattended.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from auoreo.core.approvals import connector_plugin
from auoreo.core.db import DeliveryRow, KanbanTaskRow

ATTENDED = "attended"
KINDS = (ATTENDED, "cron", "platform", "worker")


@dataclass(frozen=True)
class Origin:
    kind: str
    source: str
    agent: str | None = None
    """The origin session's agent (None when there is no such session)."""

    @property
    def unattended(self) -> bool:
        return self.kind != ATTENDED


def strict(services: Any) -> bool:
    return bool(services.settings.m7.agents.strict_origins)


async def origin_of(services: Any, session_id: str | None) -> Origin:
    if not session_id:
        return Origin(ATTENDED, "session")
    ses = await services.get_session(session_id)
    agent = ses.agent if ses is not None else None
    if (run := await _delivery(services, session_id)) is not None:
        return Origin("cron", run, agent)
    if (plugin := connector_plugin(services, session_id)) is not None:
        return Origin("platform", f"plugin:{plugin}", agent)
    if (board := await _worker_board(services, session_id)) is not None:
        return Origin("worker", f"worker:{board}", agent)
    return Origin(ATTENDED, "session", agent)


async def _delivery(services: Any, session_id: str) -> str | None:
    async with services.db.session() as s:
        q = select(DeliveryRow.kind, DeliveryRow.source).where(
            DeliveryRow.session_id == session_id, DeliveryRow.kind.in_(("job", "webhook"))
        )
        hit = (await s.execute(q.limit(1))).first()
    return f"{hit[0]}:{hit[1]}" if hit is not None else None


async def _worker_board(services: Any, session_id: str) -> str | None:
    env = os.environ.get("AUOREO_KANBAN_BOARD", "").strip()
    if env:
        return env
    teams = services.ext.get("teams") if hasattr(services, "ext") else None
    pinned = getattr(getattr(teams, "kanban", None), "worker_boards", {}).get(session_id)
    if pinned:
        return str(pinned)
    async with services.db.session() as s:
        q = select(KanbanTaskRow.board).where(KanbanTaskRow.session_id == session_id)
        board = (await s.execute(q.limit(1))).scalar()
    return str(board) if board is not None else None
