"""Typer application: wires sub-command groups from `cli/commands/`."""

from __future__ import annotations

import typer

from auoreo import __version__
from auoreo.cli.commands import config, db, doctor, harness, memory, run, secrets, serve, session, stats

app = typer.Typer(
    help="AUOREO — filesystem-native agent harness.",
    no_args_is_help=True,
    add_completion=True,
    pretty_exceptions_show_locals=False,
)
app.command("serve")(serve.serve)
app.command("run")(run.run)
app.command("doctor")(doctor.doctor)
app.add_typer(harness.app, name="harness", help="Inspect and validate definitions.")
app.add_typer(config.app, name="config", help="Read and write settings.json.")
app.add_typer(session.app, name="session", help="List and manage sessions.")
app.add_typer(secrets.app, name="secrets", help="Manage the encrypted secret store.")
app.add_typer(memory.app, name="memory", help="Search and add long-term memory.")
app.command("stats")(stats.stats)
app.add_typer(db.app, name="db", help="Migrate and inspect state.db (Alembic).")


@app.command("version")
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


# M3 control plane: tunnels, web, logs
from auoreo.cli.commands import logs, tunnel, web  # noqa: E402

app.command("web")(web.web)
app.command("logs")(logs.logs)
app.add_typer(tunnel.app, name="tunnel", help="Expose the server via cloudflared / tailscale.")


from auoreo.cli.commands import cron, deliveries, webhook  # noqa: E402

app.add_typer(cron.app, name="cron", help="Scheduled jobs: list, run now, pause, resume, runs.")
app.add_typer(
    webhook.app, name="webhook", help="Inbound webhook routes: list, add, test, rotate, deliveries, replay."
)
app.add_typer(deliveries.app, name="deliveries", help="Automation outbox: list, replay --dry-run.")
from auoreo.cli.commands import plugin  # noqa: E402

app.add_typer(plugin.app, name="plugin", help="List, add, remove, reload and pair plugins.")

# M5f: deterministic memory curator, skills lint
from auoreo.cli.commands import curator, skill  # noqa: E402

app.add_typer(
    curator.app, name="curator", help="Deterministic memory curation: run (--dry-run/--force), report."
)
app.add_typer(skill.app, name="skill", help="Skills: lint frontmatter and bodies.")

# --- W1-C: model/effort bandit
from auoreo.cli.commands import policy  # noqa: E402

app.add_typer(policy.app, name="policy", help="Adaptive turn policies: bandit status, reset.")

# --- W2-B: remote plane
from auoreo.cli.commands import daemon, env, exec_server  # noqa: E402

app.add_typer(daemon.app, name="daemon", help="Run the server detached on <home>/auoreo.sock (POSIX).")
app.command("exec-server")(exec_server.exec_server)
app.add_typer(env.app, name="env", help="Execution environments and bridge peers.")

# --- W3-A: signed self-upgrade
from auoreo.cli.commands import upgrade  # noqa: E402

app.command("upgrade")(upgrade.upgrade)

# --- F3: Perfetto trace files
from auoreo.cli.commands import trace  # noqa: E402

app.add_typer(trace.app, name="trace", help="Perfetto trace files (AUOREO_TRACE_FILE): summarize.")

# --- G5a: debug archives + exec/apply/review
from auoreo.cli.commands import debug, ops  # noqa: E402

app.add_typer(debug.app, name="debug", help="Redacted debug bundle and session snapshots (local only).")
app.command("exec")(ops.exec_)
app.command("apply")(ops.apply)
app.command("review")(ops.review)


# --- F6: out-of-process voice media sidecar
@app.command("voice-host")
def voice_host(
    check: bool = typer.Option(False, "--check", help="Start the sidecar, print its health, stop it."),
) -> None:
    """Serve the voice media sidecar on stdio (JSON-RPC), or `--check` it from the control plane."""
    if not check:
        from auoreo.voice.host import main

        main()
        return
    import asyncio

    from auoreo.cli.common import settings
    from auoreo.voice.sidecar import VoiceHost

    async def _probe() -> tuple[bool, str]:
        host = VoiceHost(settings())
        try:
            st = await host.check()
            return st.ok, st.detail if st.ok else f"{st.detail} ({st.remedy})"
        finally:
            await host.close()

    ok, detail = asyncio.run(_probe())
    typer.echo(detail)
    raise typer.Exit(0 if ok else 1)


# --- G6: skills hub (taps, quarantine, sha256 lockfile, MCP skill sources)
from auoreo.cli.commands import skills_hub  # noqa: E402

app.add_typer(
    skills_hub.app, name="skills", help="Skills hub: search, install (quarantined), approve, remove, taps."
)


# --- H4: global --profile, completion, messaging, kanban, mcp, model/agent/command/lsp,
# loop/heartbeat, login, new-feature, worktree gc, eval memory
from auoreo.cli.commands import (  # noqa: E402
    catalog,
    completion,
    evals,
    kanban,
    login,
    loop,
    lsp,
    mcp,
    messaging,
    models,
    worktree,
)
from auoreo.cli.profiles import ENV as PROFILE_ENV  # noqa: E402
from auoreo.cli.profiles import apply_profile  # noqa: E402


@app.callback()
def _global(
    profile: str | None = typer.Option(
        None, "--profile", "-p", envvar=PROFILE_ENV, help="Use ~/.auoreo/profiles/<name>/ as the home."
    ),
) -> None:
    """AUOREO — filesystem-native agent harness."""
    apply_profile(profile)


app.command("completion")(completion.completion)
app.command("messaging")(messaging.messaging)
app.add_typer(kanban.app, name="kanban", help="Kanban board: list, add, show, move, done, block.")
app.add_typer(mcp.app, name="mcp", help="MCP servers: list, add (with a connection check), rm, test.")
app.add_typer(models.app, name="model", help="Models: list routing groups, set the default, test one.")
app.add_typer(catalog.agent_app, name="agent", help="Agents: list, show, scaffold a new one.")
app.add_typer(catalog.command_app, name="command", help="Slash commands: list, show.")
app.add_typer(lsp.app, name="lsp", help="Language servers for the lsp tool: status, start, stop.")
app.command("loop")(loop.loop)
app.command("heartbeat")(loop.heartbeat)
app.command("login")(login.login)
app.command("new-feature")(worktree.new_feature)
app.add_typer(worktree.app, name="worktree", help="Feature worktrees under .auoreo/worktrees: gc.")
app.add_typer(evals.app, name="eval", help="Evals: memory retrieval (recall@10, nDCG, p95).")

# --- I4: workspace trust for <cwd>/.auoreo
from auoreo.cli.commands import trust as trust_cmd  # noqa: E402

app.command("trust")(trust_cmd.trust)
