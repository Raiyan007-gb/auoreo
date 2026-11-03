"""H4: `auoreo mcp list|add|rm|test` — MCP server definitions (`mcp/<name>.yaml`, spec §18).

`add` / `rm` edit the RUNTIME root only (`<home>/mcp/`; with `--profile` that profile's
home). Bundled and project servers are listed but never edited here. `add` validates the
file with the harness loader and runs a live connection check (initialize + tools/list)
through the same `McpBridge` the server uses; a failed check writes nothing unless
`--no-check`. Header/env values that look like secrets must be `secret:<name>` references.
`add --bundle <name>` (I6) copies a bundled definition (`mcp/{filesystem,fetch,time,memory}.yaml`,
all off by default) into the runtime root with `enabled: true`.
A running server is asked to reload its definitions afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.automation.client import local
from auoreo.cli import target
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core import mcp_bundled
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.redaction import redact
from auoreo.core.services import Services
from auoreo.harness.discovery import default_roots
from auoreo.harness.loader import LoadError
from auoreo.harness.manifests import McpServerManifest
from auoreo.harness.registry import Harness

app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]
TimeoutOpt = Annotated[float, typer.Option("--timeout", help="Seconds to wait for initialize + tools/list.")]


def _harness() -> Harness:
    st = settings()
    return Harness(default_roots(ensure_home(st.home), Path.cwd(), bundled_root(), st.extra_roots))


def _pairs(items: list[str] | None, flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"{flag} wants KEY=VALUE, got '{item}'")
        if not value.startswith("secret:") and redact(value) != value:
            raise typer.BadParameter(
                f"{flag} {key}: looks like a secret; store it (`auoreo secrets set <name>`) and pass "
                f"{key}=secret:<name>"
            )
        out[key.strip()] = value
    return out


def probe(manifest: McpServerManifest, timeout: float) -> tuple[bool, str, list[str]]:
    """Connect once with the server's own bridge: (ok, detail, tool names)."""
    from auoreo.mcp.bridge import McpBridge

    async def go(svc: Services) -> tuple[bool, str, list[str]]:
        bridge = McpBridge(svc)
        try:
            names = await bridge.connect(manifest, bridge._target(manifest), timeout=timeout)
            state = bridge.status().get(manifest.name, "error: did not start")
        finally:
            await bridge.stop()
        if state != "connected":
            return False, state, []
        return True, f"connected, {len(names)} tool(s)", names

    return local(go)


def _check(manifest: McpServerManifest, timeout: float) -> tuple[bool, str, list[str]]:
    try:
        return probe(manifest, timeout)
    except Exception as exc:  # a broken server must not crash the CLI
        return False, f"error: {exc}", []


def _reload() -> None:
    base = target.discover()
    if base is not None:
        target.call(base, "POST", "/api/harness/reload")
        console.print(f"server at {base} reloaded its definitions")


@app.command("list")
def list_(as_json: JsonOpt = False) -> None:
    """MCP servers from every definition root (runtime → project → bundled)."""
    rows: list[dict[str, Any]] = []
    for name, d in sorted(_harness().snapshot.mcp.items()):
        m = d.manifest
        rows.append(
            {
                "name": name,
                "transport": m.transport,
                "target": m.url if m.transport != "stdio" else " ".join([m.command or "", *m.args]),
                "enabled": m.enabled,
                "source": d.source,
                "path": str(d.path),
            }
        )
    if as_json:
        dump_json(rows)
        return
    table = Table(caption=None if rows else "no MCP servers defined")
    for col in ("name", "transport", "enabled", "source", "target"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["name"], r["transport"], "yes" if r["enabled"] else "no", r["source"], r["target"])
    console.print(table)


@app.command("add")
def add(
    name: Annotated[
        str | None, typer.Argument(help="Server name ([a-z][a-z0-9_]*); tools become mcp__<name>__*.")
    ] = None,
    command: Annotated[list[str] | None, typer.Argument(help="stdio: command and args (after --).")] = None,
    url: Annotated[
        str | None, typer.Option("--url", help="streamable-http endpoint instead of stdio.")
    ] = None,
    env: Annotated[list[str] | None, typer.Option("--env", help="KEY=VALUE or KEY=secret:<name>.")] = None,
    header: Annotated[list[str] | None, typer.Option("--header", help="HTTP header KEY=VALUE.")] = None,
    exposure: Annotated[str, typer.Option(help="deferred (tool_search) | direct")] = "deferred",
    disabled: Annotated[bool, typer.Option("--disabled", help="Write it with enabled: false.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing <home>/mcp/<name>.yaml.")
    ] = False,
    no_check: Annotated[bool, typer.Option("--no-check", help="Skip the live connection check.")] = False,
    timeout: TimeoutOpt = 20.0,
    bundle: Annotated[
        str | None,
        typer.Option("--bundle", help="Enable a bundled server: filesystem | fetch | time | memory."),
    ] = None,
) -> None:
    """Add an MCP server: `auoreo mcp add fs -- npx -y @modelcontextprotocol/server-filesystem .`
    or a bundled one: `auoreo mcp add --bundle time`."""
    if bundle:
        if url or command:
            raise typer.BadParameter("--bundle takes no command or --url")
        try:
            doc = mcp_bundled.bundled_doc(bundle, name, enabled=not disabled)
        except KeyError as exc:
            raise typer.BadParameter(str(exc.args[0])) from exc
        name = str(doc["name"])
    else:
        if bool(url) == bool(command) or not name:
            raise typer.BadParameter("give a name and either a stdio command (after --) or --url")
        doc = {"name": name, "exposure": exposure, "enabled": not disabled}
        if url:
            doc.update(transport="streamable-http", url=url)
        else:
            assert command is not None
            doc.update(transport="stdio", command=command[0], args=command[1:])
    if env_map := _pairs(env, "--env"):
        doc["env"] = env_map
    if headers := _pairs(header, "--header"):
        doc["headers"] = headers
    home = ensure_home(settings().home)
    dest = home / "mcp" / f"{name}.yaml"
    if dest.exists() and not force:
        err_console.print(f"[red]{dest} exists; pass --force to replace it[/red]")
        raise typer.Exit(1)
    try:
        manifest = mcp_bundled.validate_definition(doc)
    except LoadError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if not no_check:
        ok, detail, _ = _check(manifest, timeout)
        if not ok:
            err_console.print(f"[red]connection check failed: {detail}[/red] (nothing written; --no-check)")
            raise typer.Exit(1)
        console.print(f"check: {detail}")
    mcp_bundled.write_definition(home, doc, force=True)
    console.print(f"wrote {dest}")
    _reload()


@app.command("rm")
def rm(name: str) -> None:
    """Delete <home>/mcp/<name>.yaml (bundled/project servers: set `enabled: false` instead)."""
    dest = ensure_home(settings().home) / "mcp" / f"{name}.yaml"
    if not dest.is_file():
        found = _harness().snapshot.mcp.get(name)
        where = f" (defined in the {found.source} root: {found.path})" if found else ""
        err_console.print(f"[red]no runtime MCP server '{name}' in {dest.parent}{where}[/red]")
        raise typer.Exit(1)
    dest.unlink()
    console.print(f"removed {dest}")
    _reload()


@app.command("test")
def test(name: str, timeout: TimeoutOpt = 20.0, as_json: JsonOpt = False) -> None:
    """Connect to a defined server (even a disabled one) and list its tools."""
    found = _harness().snapshot.mcp.get(name)
    if found is None:
        err_console.print(f"[red]unknown MCP server '{name}'[/red]")
        raise typer.Exit(1)
    ok, detail, names = _check(found.manifest, timeout)
    if as_json:
        dump_json({"name": name, "ok": ok, "detail": detail, "tools": names})
    else:
        console.print(f"{name}: {detail}")
        for n in names:
            console.print(f"  {n}")
    if not ok:
        raise typer.Exit(1)
