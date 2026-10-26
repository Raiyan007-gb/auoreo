"""H4: `auoreo agent list|show|new` and `auoreo command list|show` (spec §18).

Read-only views over the harness registry (every definition root, highest precedence
wins), plus `agent new`, which scaffolds `agents/<name>/AGENT.md` in the runtime root
(`--project`: `<cwd>/.auoreo/`). Adding an agent is a new folder, never engine code."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.markup import escape
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.paths import bundled_root, ensure_home, project_dir
from auoreo.harness.discovery import Candidate, Root, default_roots
from auoreo.harness.loader import LoadError, load_agent
from auoreo.harness.registry import Harness

agent_app = typer.Typer(no_args_is_help=True)
command_app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]
BODY = """You are the {name} agent. {description}

- Read before you change anything; keep changes small and verifiable.
- Report the outcome first, then the details that matter.
"""


def _harness() -> Harness:
    st = settings()
    return Harness(default_roots(ensure_home(st.home), Path.cwd(), bundled_root(), st.extra_roots))


def _table(rows: list[dict[str, str]], cols: tuple[str, ...]) -> None:
    table = Table()
    for col in cols:
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(*(escape(str(r.get(c) or "")) for c in cols))
    console.print(table)


# ------------------------------------------------------------------ agents
@agent_app.command("list")
def agent_list(as_json: JsonOpt = False) -> None:
    """Agents with their source root, model and step cap."""
    h = _harness()
    rows = [
        {
            "name": a.name,
            "source": a.source,
            "model": a.manifest.model or "",
            "max_steps": str(a.manifest.max_steps),
            "description": a.manifest.description,
            "path": str(a.path),
        }
        for a in sorted(h.agents.values(), key=lambda a: a.name)
    ]
    dump_json(rows) if as_json else _table(rows, ("name", "source", "model", "max_steps", "description"))


@agent_app.command("show")
def agent_show(name: str, as_json: JsonOpt = False) -> None:
    """Frontmatter and prompt body of one agent."""
    a = _harness().agents.get(name)
    if a is None:
        err_console.print(f"[red]unknown agent '{name}'[/red]")
        raise typer.Exit(1)
    if as_json:
        dump_json(
            {
                "manifest": a.manifest.model_dump(mode="json"),
                "body": a.body,
                "source": a.source,
                "path": str(a.path),
            }
        )
        return
    console.print(f"[bold]{a.name}[/bold] ({a.source}: {a.path})")
    typer.echo(
        yaml.safe_dump(a.manifest.model_dump(mode="json", exclude_defaults=True), sort_keys=False).rstrip()
    )
    typer.echo("---")
    typer.echo(a.body.strip())


@agent_app.command("new")
def agent_new(
    name: Annotated[str, typer.Argument(help="Folder name: [a-z][a-z0-9_]*.")],
    description: Annotated[
        str, typer.Option("--description", "-d", help="One line for the agent picker.")
    ] = "",
    model: Annotated[str | None, typer.Option("--model", "-m", help="Routing group or model id.")] = None,
    tools: Annotated[
        list[str] | None, typer.Option("--tool", help="Allowed tool (repeatable; default all).")
    ] = None,
    project: Annotated[bool, typer.Option("--project", help="Write to <cwd>/.auoreo/ instead.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing AGENT.md.")] = False,
) -> None:
    """Scaffold agents/<name>/AGENT.md (validated before it is written)."""
    desc = description.strip() or f"{name.replace('_', ' ').capitalize()} agent."
    root = project_dir(Path.cwd()) if project else ensure_home(settings().home)
    dest = root / "agents" / name / "AGENT.md"
    if dest.exists() and not force:
        err_console.print(f"[red]{dest} exists; pass --force to overwrite it[/red]")
        raise typer.Exit(1)
    front: dict[str, object] = {"name": name, "description": desc}
    if model:
        front["model"] = model
    front["tools"] = {"allow": list(tools) if tools else ["*"]}
    front["skills"] = ["*"]
    text = f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n{BODY.format(name=name, description=desc)}"
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "agents" / name / "AGENT.md"
        staged.parent.mkdir(parents=True)
        staged.write_text(text, encoding="utf-8")
        try:
            load_agent(Candidate("agents", name, staged, Root("runtime", Path(tmp))))
        except LoadError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged, dest)
    console.print(f'wrote {dest}\nedit the prompt body, then: auoreo run --agent {name} "hello"')


# ------------------------------------------------------------------ slash commands
@command_app.command("list")
def command_list(as_json: JsonOpt = False) -> None:
    """Slash commands (`commands/<name>.md`)."""
    h = _harness()
    rows = [
        {
            "name": f"/{c.name}",
            "handler": c.manifest.handler,
            "args": c.manifest.args_hint,
            "source": c.source,
            "description": c.manifest.description,
        }
        for c in sorted(h.commands.values(), key=lambda c: c.name)
    ]
    dump_json(rows) if as_json else _table(rows, ("name", "handler", "args", "source", "description"))


@command_app.command("show")
def command_show(name: str, as_json: JsonOpt = False) -> None:
    """Frontmatter and template of one slash command."""
    c = _harness().commands.get(name.lstrip("/"))
    if c is None:
        err_console.print(f"[red]unknown command '{name}'[/red]")
        raise typer.Exit(1)
    if as_json:
        dump_json(
            {"manifest": c.manifest.model_dump(mode="json"), "template": c.template, "source": c.source}
        )
        return
    console.print(f"[bold]/{c.name}[/bold] ({c.source}: {c.path})")
    typer.echo(
        yaml.safe_dump(c.manifest.model_dump(mode="json", exclude_defaults=True), sort_keys=False).rstrip()
    )
    typer.echo("---")
    typer.echo(c.template.strip())
