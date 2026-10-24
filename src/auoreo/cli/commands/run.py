from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings
from auoreo.core.services import Services

_TERMINAL = {"run.completed", "run.failed", "run.blocked", "run.aborted", "run.max_steps"}


def run(
    prompt: Annotated[str, typer.Argument(help="The message to send.")],
    agent: Annotated[str | None, typer.Option(help="Agent folder name.")] = None,
    workflow: Annotated[str | None, typer.Option(help="Workflow name.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model or routing group.")] = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Continue a session.")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print events as JSON lines.")] = False,
) -> None:
    """Run one message non-interactively (tools needing approval are denied)."""
    st = settings(gateway=gateway)
    code = asyncio.run(_run(st, prompt, agent, workflow, model, session, as_json))
    raise typer.Exit(code)


async def _run(st, prompt, agent, workflow, model, session, as_json) -> int:  # type: ignore[no-untyped-def]
    svc = await Services.create(st, cwd=Path.cwd(), with_mcp=True)
    try:
        sid = session or (await svc.create_session(agent=agent, workflow=workflow, model=model)).id
        sub = svc.bus.subscribe(None)
        run_id = await svc.coordinator.submit(
            sid, prompt, agent=agent, workflow=workflow, model=model, interactive=False
        )
        status = "failed"
        while True:
            ev = await sub.queue.get()
            if as_json:
                print(json.dumps(ev.model_dump(mode="json")), flush=True)
            elif ev.aggregate_id == sid:
                _pretty(ev.type, ev.data)
            if ev.aggregate_id == sid and ev.type in _TERMINAL and ev.data.get("run_id") == run_id:
                status = ev.type.split(".", 1)[1]
                break
        await svc.coordinator.wait(sid, 5)
        if not as_json:
            err_console.print(f"[dim]session {sid} · {status}[/dim]")
        return 0 if status in {"completed", "max_steps"} else 1
    finally:
        await svc.close()


def _pretty(kind: str, data: dict) -> None:  # type: ignore[type-arg]
    def delta() -> None:
        sys.stdout.write(data.get("text", ""))
        sys.stdout.flush()

    def tool_start() -> None:
        err_console.print(f"\n[cyan]⚙ {data.get('tool')}[/cyan] {data.get('arguments', '')[:200]}")

    def tool_done() -> None:
        style = "red" if data.get("is_error") else "green"
        first = (data.get("content") or "").splitlines()[:3]
        err_console.print(f"[{style}]  ↳ {' / '.join(first)[:300]}[/{style}]")

    def failed() -> None:
        err_console.print(f"\n[red]{data.get('error') or data}[/red]")

    def step_done() -> None:
        if data.get("content"):
            print(flush=True)

    handlers = {
        "message.delta": delta,
        "tool.started": tool_start,
        "tool.completed": tool_done,
        "run.failed": failed,
        "run.blocked": failed,
        "message.completed": step_done,
    }
    handler = handlers.get(kind)
    if handler:
        handler()
