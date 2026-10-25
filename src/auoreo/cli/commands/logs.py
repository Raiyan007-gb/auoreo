"""`auoreo logs` — print durable events from `state.db` (`seq type data`), optionally following."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from auoreo.cli.common import with_services
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.core.services import Services


def logs(
    session: Annotated[str | None, typer.Option("--session", "-s", help="Only this session.")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep polling for new events.")] = False,
    after: Annotated[int, typer.Option(help="Start after this global seq.")] = 0,
    interval: Annotated[float, typer.Option(help="Poll interval in seconds when following.")] = 0.5,
) -> None:
    """Tail the event log (works while another process is serving: it reads the DB)."""
    channels = frozenset({f"session:{session}"}) if session else None

    async def go(svc: Services) -> None:
        cursor = after
        while True:
            batch = await svc.events.replay(cursor, channels)
            for ev in batch:
                cursor = ev.seq
                print(f"{ev.seq} {ev.type} {json.dumps(ev.data, default=str)}", flush=True)
            if len(batch) >= REPLAY_LIMIT:
                continue
            if not follow:
                return
            await asyncio.sleep(interval)

    try:
        with_services(go, memory=False)
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
