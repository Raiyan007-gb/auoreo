"""H4: `auoreo lsp status|start|stop` — language servers behind the `lsp` tool (spec §2.6, §25.15).

Language servers are spawned on demand by the `lsp` tool, one per (session, language,
workspace root), inside whichever process runs the session, and reaped after 10 idle
minutes or when the session ends. So from the CLI:

- `status` shows which server each language would use (settings `lsp_servers` overrides,
  then `node_modules/.bin`, then PATH) or the install remedy;
- `start <language>` launches that server for a root, completes the LSP `initialize`
  handshake and reports its capabilities (a readiness check); `--foreground` keeps it
  running until Ctrl-C;
- `stop` explains that per-session servers live in the server process (no API stops them
  from outside); they stop with their session or after the idle timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.secrets import child_env
from auoreo.tools.lsp_client import LSP_SERVERS, REMEDY, LspClient, LspUnavailable, candidates, find_root

app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]


def _languages(overrides: dict[str, list[str]]) -> list[str]:
    return sorted({*LSP_SERVERS, *overrides})


@app.command("status")
def status(
    root: Annotated[Path | None, typer.Option("--root", help="Workspace root (default: cwd).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Which language server each language resolves to (nothing is started)."""
    st = settings()
    base = (root or Path.cwd()).resolve()
    rows: list[dict[str, Any]] = []
    for lang in _languages(st.lsp_servers):
        found = candidates(lang, st.lsp_servers, base)
        rows.append(
            {
                "language": lang,
                "available": bool(found),
                "command": " ".join(found[0]) if found else None,
                "source": "settings" if lang in st.lsp_servers else ("PATH" if found else None),
                "remedy": None if found else REMEDY.get(lang, "set lsp_servers.<language> in settings.json"),
            }
        )
    if as_json:
        dump_json(rows)
        return
    table = Table(caption="servers start on demand per session inside the process running it")
    for col in ("language", "server", "note"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["language"], escape(r["command"] or "-"), escape(r["source"] or r["remedy"] or ""))
    console.print(table)


async def _start(
    lang: str, argvs: list[list[str]], root: Path, timeout_s: float, foreground: bool
) -> dict[str, Any]:
    errors: list[str] = []
    for argv in argvs:
        client = LspClient(argv, root, lang, timeout_s, child_env([], None))  # scrubbed env
        try:
            await client.start()
        except LspUnavailable as exc:
            errors.append(str(exc))
            continue
        try:
            caps = sorted(k for k, v in client.capabilities.items() if v)
            info = {
                "language": lang,
                "ok": True,
                "command": " ".join(argv),
                "root": str(root),
                "capabilities": caps,
            }
            if foreground:
                console.print(f"{lang}: {' '.join(argv)} running (root {root}); Ctrl-C to stop")
                with contextlib.suppress(asyncio.CancelledError):
                    assert client.proc is not None
                    await client.proc.wait()
            return info
        finally:
            await client.close()
    return {"language": lang, "ok": False, "error": "; ".join(errors)[:1500], "remedy": REMEDY.get(lang)}


@app.command("start")
def start(
    language: Annotated[str, typer.Argument(help="python | typescript | javascript | <lsp_servers key>")],
    root: Annotated[
        Path | None, typer.Option("--root", help="Workspace root (default: nearest marker).")
    ] = None,
    foreground: Annotated[bool, typer.Option("--foreground", help="Keep it running until Ctrl-C.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Start a language server, complete `initialize`, report its capabilities, shut it down."""
    st = settings()
    if language not in _languages(st.lsp_servers):
        err_console.print(
            f"[red]unknown language '{language}' (known: {', '.join(_languages(st.lsp_servers))})[/red]"
        )
        raise typer.Exit(2)
    cwd = Path.cwd().resolve()
    where = (root.resolve() if root else find_root(cwd, language, cwd)) or cwd
    argvs = candidates(language, st.lsp_servers, where)
    if not argvs:
        out: dict[str, Any] = {
            "language": language,
            "ok": False,
            "error": "no server found",
            "remedy": REMEDY.get(language),
        }
    else:
        try:
            out = asyncio.run(_start(language, argvs, where, st.lsp_timeout_ms / 1000, foreground))
        except KeyboardInterrupt:
            out = {"language": language, "ok": True, "command": " ".join(argvs[0]), "stopped": True}
    if as_json:
        dump_json(out)
    elif out["ok"]:
        caps = ", ".join(out.get("capabilities", [])[:12])
        console.print(f"{language}: {escape(out['command'])} initialized" + (f" ({caps})" if caps else ""))
    else:
        err_console.print(f"[red]{language}: {escape(out['error'])}[/red]")
        if out.get("remedy"):
            err_console.print(f"remedy: {out['remedy']}")
    if not out["ok"]:
        raise typer.Exit(1)


@app.command("stop")
def stop() -> None:
    """Explain how per-session language servers stop (there is nothing to stop from here)."""
    err_console.print(
        "language servers belong to the session that started them, inside the server process; "
        "they stop when the session is archived/deleted, after 10 idle minutes, or with the server. "
        "No API stops them from another process. `auoreo lsp start --foreground` servers stop with Ctrl-C."
    )
    raise typer.Exit(1)
