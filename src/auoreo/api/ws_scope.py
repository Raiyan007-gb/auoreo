"""Capability scoping for `/ws` (spec §18 `--ws-auth capability`, §24.7).

A capability-authenticated connection may see and answer only its own session's pending
permissions and questions: the `connected` snapshot is filtered, and a `control_response`
for another session's request is refused before it reaches a broker.
"""

from __future__ import annotations

from typing import Any

from auoreo.api.session_auth import ws_scope_session
from auoreo.core.approvals import pending_dict


def pending_snapshot(conn: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(pending_permissions, pending_questions)` visible to this connection."""
    sid = ws_scope_session(conn)
    svc = conn.svc
    perms = [pending_dict(p) for p in svc.approvals.pending(sid)]
    questions = [
        {"request_id": q.request_id, "session_id": q.session_id, "questions": q.questions}
        for q in svc.questions.pending(sid)
    ]
    return perms, questions


def request_session(svc: Any, request_id: str) -> str | None:
    """The session that owns a pending permission or question, or None if unknown."""
    for item in (*svc.approvals.pending(), *svc.questions.pending()):
        if item.request_id == request_id:
            return str(item.session_id)
    return None


def reply_in_scope(conn: Any, request_id: str) -> bool:
    """False only when a scoped connection answers a request that belongs to another session.
    An unknown id passes through (the broker then reports "no pending request")."""
    sid = ws_scope_session(conn)
    if sid is None:
        return True
    owner = request_session(conn.svc, request_id)
    return owner is None or owner == sid
