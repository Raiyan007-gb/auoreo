"""`auoreo policy bandit status|reset` — the model/effort bandit (W1-C, spec §12.5)."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, with_services
from auoreo.core.services import Services
from auoreo.policy.bandit import policy_of

app = typer.Typer(no_args_is_help=True)
bandit = typer.Typer(no_args_is_help=True)
app.add_typer(bandit, name="bandit", help="Contextual epsilon-greedy model/effort policy.")


@bandit.command("status")
def status(
    context: Annotated[str | None, typer.Option(help="Only this context key.")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Settings, arms, learned stats per context and recent outcomes."""

    async def go(svc: Services) -> dict[str, Any]:
        return await policy_of(svc).status()

    data = with_services(go, memory=False)
    if context is not None:
        data["stats"] = [r for r in data["stats"] if r["context_key"] == context]
        data["contexts"] = [r for r in data["contexts"] if r["context_key"] == context]
        data["recent"] = [r for r in data["recent"] if r["context_key"] == context]
    if as_json:
        dump_json(data)
        return
    state = "enabled" if data["enabled"] else "disabled (static defaults)"
    console.print(
        f"bandit: {state} · ε {data['epsilon']} − {data['epsilon_decay']}/100 pulls "
        f"(floor {data['min_epsilon']}) · contexts by {data['contexts_mode']}"
    )
    console.print("arms: " + ", ".join(a["key"] for a in data["arms"]))
    for c in data["contexts"]:
        console.print(f"context {c['context_key']}: {c['pulls']} pulls, next ε {c['epsilon']}")
    table = Table(title="arm stats")
    for col in ("context", "arm", "pulls", "mean reward"):
        table.add_column(col, justify="right" if col in {"pulls", "mean reward"} else "left")
    for r in data["stats"]:
        table.add_row(r["context_key"], r["key"], str(r["pulls"]), f"{r['mean_reward']:.3f}")
    console.print(table)


@bandit.command("reset")
def reset(
    context: Annotated[str | None, typer.Option(help="Only this context key (default: all).")] = None,
) -> None:
    """Forget learned arm stats and outcomes. Settings are unchanged."""

    async def go(svc: Services) -> tuple[int, int]:
        return await policy_of(svc).store.reset(context)

    arms, outcomes = with_services(go, memory=False)
    console.print(f"reset {context or 'all contexts'}: {arms} arm rows, {outcomes} outcomes removed")
