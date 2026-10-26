"""CLI commands carried over the daemon socket (`remote.uds.daemon_socket` decides).

`auoreo run` over the socket: note the event cursor, `POST /api/chat`, then follow
`GET /api/events?session_id=…` (SSE) until the run's terminal event — the same events the
in-process path prints, so `--format text|json|stream-json` look identical.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from auoreo.remote import uds

TERMINAL = {"run.completed", "run.failed", "run.blocked", "run.aborted", "run.max_steps"}


def _sse(resp: httpx.Response) -> Iterator[dict[str, Any]]:
    data: list[str] = []
    for line in resp.iter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []


def run_over_socket(
    sock: Path,
    prompt: str,
    *,
    agent: str | None,
    workflow: str | None,
    model: str | None,
    session: str | None,
    fmt: str,
    pretty: Callable[[str, dict[str, Any]], None],
    note: Callable[[str], None],
) -> int:
    with uds.client(sock, timeout=None) as c:
        return run_over_client(
            c,
            f"unix:{sock}",
            prompt,
            agent=agent,
            workflow=workflow,
            model=model,
            session=session,
            fmt=fmt,
            pretty=pretty,
            note=note,
        )


def run_over_client(
    c: httpx.Client,
    transport: str,
    prompt: str,
    *,
    agent: str | None,
    workflow: str | None,
    model: str | None,
    session: str | None,
    fmt: str,
    pretty: Callable[[str, dict[str, Any]], None],
    note: Callable[[str], None],
    final_text: Callable[[str], None] | None = None,
) -> int:
    """One run against any client (daemon socket or `auoreo run --attach <url>`).
    `fmt="print"` streams nothing and hands the final text to `final_text`."""
    cursor = c.get("/api/events/latest").raise_for_status().json()["seq"]
    body = {
        "message": prompt,
        "session_id": session,
        "agent": agent,
        "workflow": workflow,
        "model": model,
    }
    r = c.post("/api/chat", json={k: v for k, v in body.items() if v is not None})
    if r.status_code not in (200, 202):
        note(f"server answered {r.status_code}: {r.text}")
        return 1
    accepted = r.json()
    sid, run_id = accepted["session_id"], accepted["run_id"]
    status, final = "completed", dict[str, Any]()
    if run_id is not None:
        params: dict[str, Any] = {"session_id": sid, "after_seq": cursor}
        with c.stream("GET", "/api/events", params=params) as resp:
            for ev in _sse(resp):
                if fmt == "stream-json":
                    print(json.dumps(ev), flush=True)
                elif fmt == "text":
                    pretty(ev["type"], ev.get("data") or {})
                data = ev.get("data") or {}
                if ev["type"] in TERMINAL and data.get("run_id") == run_id:
                    status, final = ev["type"].split(".", 1)[1], data
                    break
    if fmt == "json":
        out = {
            "session_id": sid,
            "run_id": run_id,
            "status": status,
            "text": final.get("text", ""),
            "error": final.get("error"),
            "transport": transport,
        }
        print(json.dumps(out, indent=2), flush=True)
    elif fmt == "text":
        note(f"session {sid} · {status} · via {transport}")
    elif fmt == "print" and final_text is not None:
        final_text(final.get("text", "") or "")
    return 0 if status in {"completed", "max_steps"} else 1


def list_sessions(sock: Path, limit: int, archived: bool) -> list[dict[str, str]]:
    with uds.client(sock) as c:
        r = c.get("/api/sessions", params={"limit": limit, "archived": archived}).raise_for_status()
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "agent": s["agent"],
            "status": s["status"],
            "updated": s["updated_at"],
        }
        for s in r.json()
    ]
