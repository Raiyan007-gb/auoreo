"""H4: `auoreo loop <interval> <prompt>` and `auoreo heartbeat` (spec §18 `cron|loop|heartbeat`).

`loop` repeats a prompt (or `/command`) every INTERVAL in one session until Ctrl-C or
`--limit` (default cap: `cli2.loop_max_iterations`). Turns are non-interactive like
`auoreo run`: tools that need approval are denied unless allowed by rules.

`heartbeat` is a proactive check-in: every `--every` (default `cli2.heartbeat_every_s`)
the agent reads the HEARTBEAT.md checklist (`<cwd>/.auoreo/` then `<home>/`) and either
answers the OK token (printed as one dim line on stderr, nothing on stdout) or reports
what needs attention (printed). `--once` runs a single beat (for cron / scripts)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from auoreo.cli.commands.run import _pretty
from auoreo.cli.common import console, err_console, settings
from auoreo.cli.loop_support import (
    Beat,
    heartbeat_file,
    heartbeat_prompt,
    is_quiet,
    parse_interval,
    run_repeat,
    stamp,
)
from auoreo.cli.run_support import RunPlan

FORMATS = ("text", "print", "json")
AgentOpt = Annotated[str | None, typer.Option("--agent", help="Agent folder name.")]
ModelOpt = Annotated[str | None, typer.Option("--model", "-m", help="Model or routing group.")]
SessionOpt = Annotated[str | None, typer.Option("--session", "-s", help="Continue this session.")]
LimitOpt = Annotated[int | None, typer.Option("--limit", "-n", min=1, help="Stop after N iterations.")]


def _interval(text: str) -> float:
    try:
        return parse_interval(text)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def loop(
    interval: Annotated[str, typer.Argument(help="30s | 5m | 1h30m | seconds")],
    prompt: Annotated[str, typer.Argument(help="Message (or /command args) to send each time.")],
    limit: LimitOpt = None,
    agent: AgentOpt = None,
    model: ModelOpt = None,
    session: SessionOpt = None,
    fresh: Annotated[bool, typer.Option("--fresh", help="A new session for every iteration.")] = False,
    fmt: Annotated[str, typer.Option("--format", help="text | print | json (one object per line)")] = "text",
) -> None:
    """Repeat PROMPT every INTERVAL until Ctrl-C or --limit."""
    if fmt not in FORMATS:
        raise typer.BadParameter(f"--format must be one of {', '.join(FORMATS)}")
    if not prompt.strip():
        raise typer.BadParameter("prompt must not be empty")
    st = settings()
    every = _interval(interval)
    cap = limit or st.cli2.loop_max_iterations
    plan = RunPlan(
        prompt=prompt, agent=agent, model=model, session=session, fmt="quiet" if fmt == "json" else fmt
    )

    def show(b: Beat) -> None:
        if fmt == "json":
            print(
                json.dumps(
                    {"iteration": b.index, "session_id": b.session_id, "status": b.status, "text": b.text}
                ),
                flush=True,
            )
        else:
            err_console.print(f"[dim]#{b.index}/{cap} {stamp()} · {b.status} · next in {interval}[/dim]")

    beats, _ = run_repeat(st, plan, interval_s=every, limit=cap, fresh=fresh, on_beat=show, pretty=_pretty)
    if beats and beats[-1].status not in {"completed", "max_steps"}:
        raise typer.Exit(1)


def heartbeat(
    every: Annotated[
        str | None, typer.Option("--every", help="Interval (default cli2.heartbeat_every_s).")
    ] = None,
    once: Annotated[bool, typer.Option("--once", help="One beat, then exit (for cron/scripts).")] = False,
    limit: LimitOpt = None,
    agent: AgentOpt = None,
    model: ModelOpt = None,
    session: SessionOpt = None,
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="Checklist text instead of HEARTBEAT.md.")
    ] = None,
) -> None:
    """Proactive check-in on an interval; quiet unless something needs attention."""
    from pathlib import Path

    st = settings()
    interval_s = _interval(every) if every else st.cli2.heartbeat_every_s
    source = heartbeat_file(st, Path.cwd())
    checklist = prompt if prompt is not None else (source.read_text(encoding="utf-8") if source else "")
    if prompt is None:
        err_console.print(f"[dim]checklist: {source or f'none ({st.cli2.heartbeat_file} not found)'}[/dim]")
    plan = RunPlan(prompt="", agent=agent, model=model, session=session, title="heartbeat", fmt="quiet")
    token = st.cli2.heartbeat_ok_token

    def show(b: Beat) -> None:
        if b.status not in {"completed", "max_steps"}:
            err_console.print(f"[red]{stamp()} heartbeat #{b.index} {b.status}[/red]")
        elif is_quiet(b.text, token):
            err_console.print(f"[dim]{stamp()} heartbeat #{b.index}: {token}[/dim]")
        else:
            console.print(f"[bold]{stamp()} heartbeat #{b.index}[/bold] (session {b.session_id})")
            print(b.text.strip(), flush=True)

    cap = 1 if once else (limit or 10**9)
    beats, _ = run_repeat(
        st,
        plan,
        interval_s=interval_s,
        limit=cap,
        prompt_for=lambda _i: heartbeat_prompt(st, checklist),
        on_beat=show,
    )
    if beats and beats[-1].status not in {"completed", "max_steps"}:
        raise typer.Exit(1)
