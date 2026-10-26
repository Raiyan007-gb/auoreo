"""`auoreo logs` — print durable events from `state.db` (`seq type data`), optionally following.
`--level` (I5) keeps events at or above a severity (`core/event_levels.py`)."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from auoreo.cli.common import err_console, with_services
from auoreo.core.event_levels import LEVELS, at_least, level_of
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.core.services import Services


def logs(
    session: Annotated[str | None, typer.Option("--session", "-s", help="Only this session.")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep polling for new events.")] = False,
    after: Annotated[int, typer.Option(help="Start after this global seq.")] = 0,
    interval: Annotated[float, typer.Option(help="Poll interval in seconds when following.")] = 0.5,
    level: Annotated[
        str | None, typer.Option("--level", "-l", help="Minimum severity: debug | info | warn | error.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="One JSON event per line (with `level`).")] = False,
) -> None:
    """Tail the event log (works while another process is serving: it reads the DB)."""
    if level is not None and level not in LEVELS:
        err_console.print(f"[red]--level must be one of {', '.join(LEVELS)}[/red]")
        raise typer.Exit(2)
    channels = frozenset({f"session:{session}"}) if session else None

    async def go(svc: Services) -> None:
        cursor = after
        while True:
            batch = await svc.events.replay(cursor, channels)
            for ev in batch:
                cursor = ev.seq
                if level is not None and not at_least(ev.type, ev.data, level):
                    continue
                if as_json:
                    row = {**ev.model_dump(mode="json"), "level": level_of(ev.type, ev.data)}
                    print(json.dumps(row, default=str), flush=True)
                else:
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
