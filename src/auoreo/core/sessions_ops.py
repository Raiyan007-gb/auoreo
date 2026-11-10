"""Session lifecycle operations: fork, archive, delete, export (spec M3c).

Kept out of `services.py` so the composition root stays small. Every operation that
changes history refuses while the session is running (`SessionBusyError` → HTTP 409).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from auoreo.core.db import MessageRow, SessionRow

if TYPE_CHECKING:
    from auoreo.core.services import Services

TOOL_RESULT_PREVIEW = 200


class SessionBusyError(RuntimeError):
    """The session has a turn in flight; retry once it is idle."""


def session_info(row: SessionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "agent": row.agent,
        "workflow": row.workflow,
        "model": row.model,
        "status": row.status,
        "parent_id": row.parent_id,
        "archived": bool(row.archived),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def _require(svc: Services, session_id: str, *, idle: bool = False) -> SessionRow:
    row = await svc.get_session(session_id)
    if row is None:
        raise KeyError(f"unknown session '{session_id}'")
    if idle and svc.coordinator.is_running(session_id):
        raise SessionBusyError(f"session '{session_id}' is running; cancel it or wait")
    return row


def fork_title(title: str | None) -> str | None:
    """`<title> (fork)` so a fork is told apart from its source in lists; never stacks the suffix."""
    if not title:
        return title
    return title if title.endswith(" (fork)") else f"{title} (fork)"


async def fork_session(svc: Services, session_id: str) -> SessionRow:
    """New session with the same agent/workflow/model and a copy of every committed message."""
    parent = await _require(svc, session_id, idle=True)
    messages = await svc.coordinator.runner.load_history(session_id)
    child = await svc.create_session(
        title=fork_title(parent.title),
        agent=parent.agent,
        workflow=parent.workflow,
        model=parent.model,
        parent_id=parent.id,
    )
    if messages:
        async with svc.db.session() as s:
            s.add_all(MessageRow(session_id=child.id, idx=i, message=m) for i, m in enumerate(messages))
            await s.commit()
    at_idx = len(messages) - 1
    await svc.emit(child.id, "session.forked", {"parent_id": parent.id, "at_idx": at_idx})
    await svc.emit(parent.id, "session.fork_created", {"child_id": child.id, "at_idx": at_idx})
    return child


async def set_archived(svc: Services, session_id: str, archived: bool) -> SessionRow:
    await _require(svc, session_id)
    row = await svc.update_session(session_id, archived=archived)
    assert row is not None
    await svc.emit(session_id, "session.archived" if archived else "session.unarchived", {})
    return row


async def delete_session(svc: Services, session_id: str) -> None:
    """Delete the row and its messages. Events are append-only and stay."""
    await _require(svc, session_id, idle=True)
    async with svc.db.session() as s:
        await s.execute(delete(MessageRow).where(MessageRow.session_id == session_id))
        row = await s.get(SessionRow, session_id)
        if row is not None:
            await s.delete(row)
        await s.commit()
    svc.coordinator.forget(session_id)
    await svc.close_backend_session(session_id)
    await svc.emit("server", "session.deleted", {"session_id": session_id})


async def export_session(svc: Services, session_id: str, fmt: str = "json") -> str:
    from auoreo.runtime.request_layout import plain  # M7: persisted context blocks stay private

    row = await _require(svc, session_id)
    async with svc.db.session() as s:
        q = select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.idx)
        messages = plain(dict(r.message) for r in (await s.execute(q)).scalars())
    exporters = {"json": _export_json, "md": _export_md}
    if fmt not in exporters:
        raise ValueError(f"unknown export format '{fmt}' (use json or md)")
    return exporters[fmt](row, messages)


def _export_json(row: SessionRow, messages: list[dict[str, Any]]) -> str:
    return json.dumps({"session": session_info(row), "messages": messages}, indent=2, default=str)


def _export_md(row: SessionRow, messages: list[dict[str, Any]]) -> str:
    """Readable transcript; each tool call collapses to `**tool** name(args) → result`."""
    results = {m.get("tool_call_id"): str(m.get("content") or "") for m in messages if m["role"] == "tool"}
    lines = [
        f"# {row.title or row.id}",
        "",
        f"- session: `{row.id}`  agent: `{row.agent}`  workflow: `{row.workflow}`"
        + (f"  model: `{row.model}`" if row.model else ""),
        f"- created: {row.created_at.isoformat()}",
        "",
    ]
    for m in messages:
        role = m["role"]
        if role == "tool":
            continue
        content = str(m.get("content") or "").strip()
        if role == "system":
            lines += ["> **system:** " + content.replace("\n", "\n> "), ""]
            continue
        lines += [f"## {role.capitalize()}", ""]
        if content:
            lines += [content, ""]
        for call in m.get("tool_calls") or []:
            fn = call.get("function", {})
            result = results.get(call.get("id"), "(no result)")
            preview = " ".join(result.split())[:TOOL_RESULT_PREVIEW]
            lines += [f"**tool** {fn.get('name')}({fn.get('arguments', '')}) → {preview}", ""]
    return "\n".join(lines).rstrip() + "\n"
