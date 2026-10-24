---
name: cli-typer
description: Build the agent_cli command surface with Typer — subcommands (serve/run/login/session), Annotated args/options, invoke_without_command, running the same server. Use when building agent_cli in this repo.
---

# Typer — agent_cli command surface (plan.md Part D)

`agent_cli` (package `agent-cli`, command `agent`) is built with **Typer** (Click-adjacent, type-hint-driven). It ships the easy install (`pipx install agent-cli` / `uv tool install`) and runs **the same server** as everything else via `serve` (plan.md Part C.1).

Docs: https://typer.tiangolo.com · Context7: `/websites/typer_tiangolo` (77).

## App + subcommands

```python
# src/agent_cli/main.py
import typer
from typing_extensions import Annotated    # or typing.Annotated on 3.12

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
    listener = agent_api.listen(hostname=hostname, port=port)   # reuse server core
    print(f"agent-platform server listening on http://{listener.hostname}:{listener.port}", flush=True)
    listener.serve_forever()   # Effect.never-style: block until signal
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
app.add_typer(config_app, name="config")

@config_app.command("get")
def config_get(key: str) -> None: ...
```

## invoke_without_command

If you want `agent` (no args) to show help or drop into the TUI, use:

```python
app = typer.Typer(invoke_without_command=True)
@app.callback()
def main_callback() -> None:
    # e.g. print help, or auto-start `agent serve` if desired
```

## Rules for this repo

- Keep Typer imports ONLY in `agent_cli`; reuse logic from `agent_core`/`agent_api` (plan.md import-boundary rule).
- `agent run` must accept `--port 0` for auto-pick and print/resolve the URL (same discovery contract).
- Add a `--json` flag to key commands for scriptability (models, sessions) — pairs with the Python SDK.
- `pyproject.toml` entry point: `[project.scripts] agent = "agent_cli.main:app"` so `uv build`/PyPI/npm-launcher all produce the `agent` binary (see `uv` + `native-binary` skills).
- Test via the SDK/httpx against a spawned server, not by mocking the CLI internals (pytest e2e — see `pytest` skill).