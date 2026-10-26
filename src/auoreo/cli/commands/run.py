from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings
from auoreo.cli.run_support import (
    RunPlan,
    RunUsageError,
    compose_prompt,
    parse_rule,
    run_attached,
    run_in_process,
)
from auoreo.core.settings import Settings
from auoreo.runtime.inbox import RunOptions

FORMATS = ("text", "json", "stream-json")
EFFORTS = ("low", "medium", "high", "max")


def run(
    prompt: Annotated[str | None, typer.Argument(help="The message ('-' reads stdin).")] = None,
    agent: Annotated[str | None, typer.Option(help="Agent folder name.")] = None,
    workflow: Annotated[str | None, typer.Option(help="Workflow name.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model or routing group.")] = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Continue a session.")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            help="text (streamed prose) | json (final result object) | stream-json (JSON-lines events)",
        ),
    ] = "text",
    as_json: Annotated[bool, typer.Option("--json", help="Alias of --format stream-json.")] = False,
    # --- G5a: spec §18 flags (machinery in cli/run_support.py)
    print_only: Annotated[bool, typer.Option("--print", help="Print only the final answer text.")] = False,
    interactive: Annotated[
        bool, typer.Option("--interactive", "-i", help="Prompt loop; asks are answered here.")
    ] = False,
    bare: Annotated[bool, typer.Option("--bare", help="System prompt = the agent body only.")] = False,
    files: Annotated[
        list[Path] | None, typer.Option("--file", "-f", help="Attach a file (repeatable, 10 MiB each).")
    ] = None,
    fork: Annotated[str | None, typer.Option("--fork", help="Fork this session and run in the fork.")] = None,
    cont: Annotated[bool, typer.Option("--continue", "-c", help="Continue the most recent session.")] = False,
    title: Annotated[str | None, typer.Option("--title", help="Title for a new session.")] = None,
    variant: Annotated[
        str | None, typer.Option(help="Shape requests by the variants.yaml entry matching this id.")
    ] = None,
    thinking: Annotated[str | None, typer.Option(help="Reasoning effort: low|medium|high|max.")] = None,
    attach: Annotated[str | None, typer.Option("--attach", help="Run against a running server URL.")] = None,
    allow: Annotated[
        list[str] | None, typer.Option("--allow", help="Session rule <perm>[:<pattern>] (repeatable).")
    ] = None,
    deny: Annotated[list[str] | None, typer.Option("--deny", help="Session deny rule (repeatable).")] = None,
    auto: Annotated[
        bool, typer.Option("--auto", help="Auto-approve asks (floors/denies still apply).")
    ] = False,
) -> None:
    """Run a message (non-interactive by default: tools needing approval are denied)."""
    if as_json:
        fmt = "stream-json"
    if fmt not in FORMATS:
        err_console.print(f"[red]--format must be one of {', '.join(FORMATS)}[/red]")
        raise typer.Exit(2)
    try:
        flags = Flags(print_only, interactive, bare, files or [], fork, cont, title, variant, thinking)
        plan = build_plan(prompt, agent, workflow, model, session, fmt, flags, attach, allow, deny, auto)
        code = _dispatch(settings(gateway=gateway), plan, attach)
    except RunUsageError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except KeyError as exc:
        err_console.print(f"[red]{str(exc).strip(chr(39) + chr(34))}[/red]")
        raise typer.Exit(1) from exc
    raise typer.Exit(code)


def _dispatch(st: Settings, plan: RunPlan, attach: str | None) -> int:
    if attach:
        return run_attached(attach, plan, _pretty)
    from auoreo.remote import cli_client, uds  # --- W2-B: a live daemon socket wins

    sock = uds.daemon_socket(st.home)
    if sock is None or plan.local_only or plan.fmt == "print":
        return asyncio.run(run_in_process(st, plan, _pretty))
    if plan.fork or plan.cont or plan.title:
        return run_attached(f"unix:{sock}", plan, _pretty)
    return cli_client.run_over_socket(
        sock,
        plan.prompt,
        agent=plan.agent,
        workflow=plan.workflow,
        model=plan.model,
        session=plan.session,
        fmt=plan.fmt,
        pretty=_pretty,
        note=lambda msg: err_console.print(f"[dim]{msg}[/dim]"),
    )


@dataclass(frozen=True)
class Flags:
    print_only: bool
    interactive: bool
    bare: bool
    files: list[Path]
    fork: str | None
    cont: bool
    title: str | None
    variant: str | None
    thinking: str | None


def _validate(f: Flags, session: str | None, fmt: str, attach: str | None) -> None:
    if f.print_only and f.interactive:
        raise RunUsageError("--print and --interactive are mutually exclusive")
    if f.interactive and (attach or fmt != "text"):
        raise RunUsageError("--interactive runs in-process with text output (no --attach/--format)")
    if sum(map(bool, (f.fork, f.cont, session))) > 1:
        raise RunUsageError("use only one of --session, --continue, --fork")
    if f.thinking is not None and f.thinking not in EFFORTS:
        raise RunUsageError(f"--thinking must be one of {', '.join(EFFORTS)}")


def build_plan(
    prompt: str | None,
    agent: str | None,
    workflow: str | None,
    model: str | None,
    session: str | None,
    fmt: str,
    f: Flags,
    attach: str | None,
    allow: list[str] | None,
    deny: list[str] | None,
    auto: bool,
) -> RunPlan:
    """Validate flag combinations and build the plan (raises RunUsageError → exit 2)."""
    _validate(f, session, fmt, attach)
    if prompt == "-" or (prompt is None and not f.interactive and not sys.stdin.isatty()):
        prompt = sys.stdin.read()
    if not (prompt or "").strip() and not f.interactive:
        raise RunUsageError("a prompt is required (or use --interactive)")
    rules = [parse_rule(r, "allow") for r in allow or []] + [parse_rule(r, "deny") for r in deny or []]
    wants_options = f.bare or f.variant or f.thinking
    plan = RunPlan(
        prompt=compose_prompt(prompt or "", f.files),
        agent=agent,
        workflow=workflow,
        model=model,
        session=session,
        fork=f.fork,
        cont=f.cont,
        title=f.title,
        fmt="print" if f.print_only else fmt,
        interactive=f.interactive,
        auto=auto,
        rules=rules,
        options=RunOptions(bare=f.bare, variant=f.variant, effort=f.thinking) if wants_options else None,
    )
    if attach and (plan.rules or plan.auto or plan.options):
        raise RunUsageError(
            "--allow/--deny/--auto/--bare/--variant/--thinking need an in-process run; "
            "set them on the server (settings.json / agent frontmatter) when using --attach"
        )
    return plan


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
