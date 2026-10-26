from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, with_services
from auoreo.core.services import Services
from auoreo.core.usage import UsageLedger


def stats(
    days: Annotated[int, typer.Option(help="Look-back window in days.")] = 7,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    project: Annotated[
        bool, typer.Option("--project", help="Group by project (the cwd a session was created in).")
    ] = False,
    by_subagent: Annotated[
        bool, typer.Option("--by-subagent", help="Group by delegated subagent (spec §25.15).")
    ] = False,
) -> None:
    """Token and cost usage per model and per session (or per project / subagent)."""
    if project:  # --- G5a
        _by_project(days, as_json)
        return
    if by_subagent:  # --- I3
        _by_subagent(days, as_json)
        return

    async def go(svc: Services) -> dict[str, Any]:
        return await UsageLedger(svc.db).summary(days)

    data = with_services(go, memory=False)
    if as_json:
        dump_json(data)
        return
    for title, rows in (("model", data["by_model"]), ("session", data["by_session"])):
        table = Table(title=f"usage by {title} (last {days}d)")
        for col in (title, "calls", "prompt", "completion", "cost $"):
            table.add_column(col, justify="left" if col == title else "right")
        for r in [*rows, data["total"]]:
            table.add_row(
                r["key"],
                str(r["calls"]),
                f"{r['prompt_tokens']:,}",
                f"{r['completion_tokens']:,}",
                f"{r['cost_usd']:.4f}",
            )
        console.print(table)


def _by_project(days: int, as_json: bool) -> None:
    from auoreo.core.usage_projects import by_project

    async def go(svc: Services) -> list[dict[str, Any]]:
        return await by_project(svc.db, days)

    rows = with_services(go, memory=False)
    if as_json:
        dump_json({"days": days, "by_project": rows})
        return
    table = Table(title=f"usage by project (last {days}d)")
    for col in ("project", "sessions", "calls", "prompt", "completion", "cost $"):
        table.add_column(col, justify="left" if col == "project" else "right")
    for r in rows:
        table.add_row(
            r["key"],
            str(r["sessions"]),
            str(r["calls"]),
            f"{r['prompt_tokens']:,}",
            f"{r['completion_tokens']:,}",
            f"{r['cost_usd']:.4f}",
        )
    console.print(table)


def _by_subagent(days: int, as_json: bool) -> None:
    from auoreo.core.usage_attrib import by_subagent

    async def go(svc: Services) -> list[dict[str, Any]]:
        return await by_subagent(svc.db, days)

    rows = with_services(go, memory=False)
    if as_json:
        dump_json({"days": days, "by_subagent": rows})
        return
    table = Table(title=f"usage by subagent (last {days}d)")
    for col in ("subagent", "task", "parent", "depth", "calls", "prompt", "completion", "cost $"):
        table.add_column(col, justify="left" if col in ("subagent", "task", "parent") else "right")
    for r in rows:
        table.add_row(
            r["subagent_id"],
            r["task_name"],
            r["parent_session_id"],
            str(r["depth"]),
            str(r["calls"]),
            f"{r['prompt_tokens']:,}",
            f"{r['completion_tokens']:,}",
            f"{r['cost_usd']:.4f}",
        )
    console.print(table)
