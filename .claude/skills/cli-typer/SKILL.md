---
name: cli-typer
description: Build the agent_cli command surface with Typer — subcommands (serve/run/login/session), Annotated args/options, invoke_without_command, running the same server. Use when building agent_cli in this repo.
---

# Typer — agent_cli command surface (plan.md Part D)

`agent_cli` (package `agent-cli`, command `agent`) is built with **Typer** (Click-adjacent, type-hint-driven). It ships the easy install (`pipx install agent-cli` / `uv tool install`) and runs **the same server** as everything else via `serve` (plan.md Part C.1).

Docs: https://typer.tiangolo.com · Context7: `/websites/typer_tiangolo`. Typer is still 0.x (0.23.0 shipped 2026-02); pin `typer>=0.21.2` or newer.

## App + subcommands

```python
# src/agent_cli/main.py
from typing import Annotated  # stdlib; Typer ≥0.21.2 no longer depends on typing-extensions
import typer

app = typer.Typer(help="Agent Platform CLI")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

Each command is a function on `app`:

```python
@app.command()
def serve(
    hostname: Annotated[str, typer.Option(help="hostname to listen on")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="port to listen on", min=0, max=65535)] = 0,
) -> None:
    """Start the headless server (prints the URL line for SDKs/TUI/web to attach)."""
    listener = agent_api.listen(hostname=hostname, port=port)  # reuse server core
    print(f"agent-platform server listening on http://{listener.hostname}:{listener.port}", flush=True)
    listener.serve_forever()  # Effect.never-style: block until signal
```

- Arguments = `Annotated[str, typer.Argument()]`; Options = `typer.Option(help=..., min=..., max=...)`. Types carry the validation.
- Prints the URL line **to stdout immediately** — that's the discovery contract (see `sdk-codegen` skill / opencode `createOpencodeServer` waits for it).

## Full command set (plan.md Part D agent_cli/)

| File | Command | Purpose |
|---|---|---|
| `serve.py` | `agent serve [--hostname] [--port] [--mdns] [--cors]` | Headless server, one port |
| `run.py` | `agent run "prompt" [--model] [--agent] [--session]` | One-shot: boot/attach server, run, print result |
| `login.py` | `agent login [provider]` | Provider OAuth/PKCE + env-var check (see `auth-oauth`) |
| `session.py` | `agent session list|show|resume [--id]` | Manage sessions |
| `models.py` / `providers.py` | `agent models`, `agent providers` | List models/providers, login |
| `tools.py` / `skills.py` / `commands.py` | list/manage | Registry introspection |
| `mcp.py` | `agent mcp add|rm|list` | MCP server registry |
| `plugin.py` | `agent plugin install|list` | Plugin hooks |
| `config.py` | `agent config get|set|path` | Config (plan.md core/config) |
| `upgrade.py` | `agent upgrade` | Self-update (see `npm` skill upgrade) |

## Running the CLI properly

```python
# In each command file, separate the Typer wiring from the logic.
# Logic lives in agent_core/agent_api; agent_cli is a thin presentation shell.
def run(server_url: str, prompt: str, ...) -> None:
    client = agent_sdk.attach(server_url)       # typed client (generated)
    session = client.post("/session/create", ...)
    ...  # subscribe to /api/event, stream parts to stdout
```

- Call the **SDK**, not HTTP directly — keeps CLI + API + tests on one contract (see `sdk-codegen`).
- Handle `Ctrl-C`/signals: map `KeyboardInterrupt` → clean `servershutdown`; forward SIGINT/SIGTERM like the launcher (see `npm` skill `bin/agent`).

## Group-of-commands pattern (nice UX)

For config/session sub-groups, nest Typer instances:

```python
config_app = typer.Typer(help="Get/set configuration")
app.add_typer(
    config_app, name="config"
)  # pass name= explicitly (since 0.14 it's no longer inferred from the callback)


@config_app.command("get")
def config_get(key: str) -> None: ...
```

## invoke_without_command

By default `agent` (no args) just shows help. To run logic instead (e.g. drop into the TUI), make the callback executable and check `ctx.invoked_subcommand` so it doesn't also fire before every subcommand:

```python
@app.callback(invoke_without_command=True)  # or typer.Typer(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        ...  # e.g. launch TUI, or auto-start `agent serve`
```

For sub-groups that should print help when called bare, use `add_typer(..., no_args_is_help=True)`.

`agent --version` (required by `native-binary` rules): an eager `bool` option on the callback whose `callback=` prints the version and `raise typer.Exit()`.

## Rules for this repo

- Keep Typer imports ONLY in `agent_cli`; reuse logic from `agent_core`/`agent_api` (plan.md import-boundary rule).
- `agent run` must accept `--port 0` for auto-pick and print/resolve the URL (same discovery contract).
- Add a `--json` flag to key commands for scriptability (models, sessions) — pairs with the Python SDK.
- `pyproject.toml` entry point: `[project.scripts] agent = "agent_cli.main:app"` so `uv build`/PyPI/npm-launcher all produce the `agent` binary (see `uv` + `native-binary` skills).
- Test via the SDK/httpx against a spawned server, not by mocking the CLI internals (pytest e2e — see `pytest` skill).