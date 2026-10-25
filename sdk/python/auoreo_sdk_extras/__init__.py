"""Hand-written helpers on top of the generated `auoreo_client` package.

- `subscribe_events(...)`: follow `GET /api/events` (SSE) with automatic `Last-Event-ID`
  resume after a dropped connection.
- `chat_and_wait(client, message)`: queue a chat message and poll the event stream until the
  run reaches a terminal `run.*` event; returns the final text.

Only `httpx` is required (it is already a dependency of the generated client).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

__all__ = ["TERMINAL_RUN_EVENTS", "chat_and_wait", "iter_sse", "subscribe_events"]

TERMINAL_RUN_EVENTS = frozenset(
    {"run.completed", "run.failed", "run.blocked", "run.aborted", "run.max_steps"}
)
Event = dict[str, Any]
OnEvent = Callable[[Event], Awaitable[bool | None] | bool | None]
"""Return True to stop the subscription."""


def iter_sse(lines: Iterable[str]) -> Iterable[tuple[str | None, str]]:
    """Parse SSE lines into `(id, data)` pairs; comments and keep-alives are skipped."""
    event_id: str | None = None
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield event_id, "\n".join(data)
            event_id, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            data.append(value)
        elif field == "id":
            event_id = value
    if data:
        yield event_id, "\n".join(data)


def _headers(token: str | None, last_id: int) -> dict[str, str]:
    headers = {"Accept": "text/event-stream", "Last-Event-ID": str(last_id)}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def subscribe_events(
    base_url: str,
    token: str | None,
    after_seq: int,
    on_event: OnEvent,
    *,
    session_ids: list[str] | None = None,
    follow: bool = True,
    reconnect_delay_s: float = 1.0,
    max_reconnects: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Stream events with `seq > after_seq` to `on_event` until it returns True (or the
    replay ends when `follow=False`). Reconnects with `Last-Event-ID: <last seq>` after a
    dropped connection. Returns the last seq seen."""
    last = after_seq
    attempts = 0
    params: list[tuple[str, str | bool]] = [("follow", follow)]
    params += [("session_id", s) for s in session_ids or []]
    async with httpx.AsyncClient(base_url=base_url, timeout=None, transport=transport) as http:
        while True:
            try:
                async with http.stream(
                    "GET", "/api/events", params=params, headers=_headers(token, last)
                ) as resp:
                    resp.raise_for_status()
                    buf: list[str] = []
                    async for line in resp.aiter_lines():
                        buf.append(line)
                        if line.strip():
                            continue
                        for _eid, data in iter_sse(buf):
                            event: Event = json.loads(data)
                            last = max(last, int(event.get("seq", last)))
                            stop = on_event(event)
                            if inspect.isawaitable(stop):
                                stop = await stop
                            if stop:
                                return last
                        buf = []
                if not follow:
                    return last
            except (httpx.TransportError, httpx.RemoteProtocolError):
                attempts += 1
                if max_reconnects is not None and attempts > max_reconnects:
                    raise
                await asyncio.sleep(reconnect_delay_s)


async def chat_and_wait(
    client: Any,
    message: str,
    *,
    session_id: str | None = None,
    timeout_s: float = 300.0,
    poll_s: float = 0.25,
) -> str:
    """`POST /api/chat`, then poll `GET /api/events?follow=false` until this run's terminal
    `run.*` event. `client` is a generated `auoreo_client.Client`/`AuthenticatedClient` (or
    anything with `get_async_httpx_client()`). Raises `RuntimeError` on a non-completed run."""
    http: httpx.AsyncClient = client.get_async_httpx_client()
    body: dict[str, Any] = {"message": message}
    if session_id:
        body["session_id"] = session_id
    r = await http.post("/api/chat", json=body)
    r.raise_for_status()
    accepted = r.json()
    sid, run_id = accepted["session_id"], accepted["run_id"]
    cursor = 0
    async with asyncio.timeout(timeout_s):
        while True:
            resp = await http.get(
                "/api/events",
                params={"session_id": sid, "follow": "false"},
                headers={"Last-Event-ID": str(cursor)},
            )
            resp.raise_for_status()
            for _eid, data in iter_sse(resp.text.splitlines()):
                event = json.loads(data)
                cursor = max(cursor, int(event.get("seq", cursor)))
                payload = event.get("data") or {}
                if event.get("type") in TERMINAL_RUN_EVENTS and payload.get("run_id") == run_id:
                    if event["type"] in {"run.completed", "run.max_steps"}:
                        return str(payload.get("text", ""))
                    raise RuntimeError(f"{event['type']}: {payload.get('error') or payload}")
            await asyncio.sleep(poll_s)
