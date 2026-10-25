"""`auoreo plugin list|add|remove|reload|pair`.

Commands talk to a running server first (`--url`, `AUOREO_URL`, else `http://<host>:<port>`
from settings) and fall back to working on disk when none answers: `list` then reports
discovery + lock state (never `running`), `pair` edits the allowlist in `settings.json`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.table import Table

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Candidate, Root, default_roots
from auoreo.harness.loader import LoadError, load_plugin
from auoreo.harness.registry import Harness
from auoreo.plugins.connectors import PairingStore, file_config
from auoreo.plugins.install import folder_hash, verify

app = typer.Typer(no_args_is_help=True)
UrlOpt = Annotated[
    str | None, typer.Option("--url", help="Server base URL (default: AUOREO_URL or settings).")
]
COLUMNS = ("name", "version", "status", "provides", "tools", "verify")
COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "__pycache__", "*.pyc", "lock.json", "pairing.json", "sessions.json"
)


def _base(st: Settings, url: str | None) -> tuple[str, bool]:
    explicit = url or os.environ.get("AUOREO_URL")
    return (explicit or f"http://{st.host}:{st.port}").rstrip("/"), bool(explicit)


def _api(st: Settings, url: str | None, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """JSON from the running server, or None when no server answers (implicit URL only)."""
    base, explicit = _base(st, url)
    headers = {"Authorization": f"Bearer {st.server_password}"} if st.server_password else {}
    try:
        resp = httpx.request(method, base + path, json=body, headers=headers, timeout=120)
    except httpx.TransportError as exc:
        if explicit:
            err_console.print(f"[red]cannot reach {base}: {exc}[/red]")
            raise typer.Exit(1) from exc
        return None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        err_console.print(f"[red]{resp.status_code}: {detail}[/red]")
        raise typer.Exit(1)
    return resp.json()


def _offline_rows(st: Settings) -> list[dict[str, Any]]:
    from auoreo.plugins.host import validate_config

    home = ensure_home(st.home)
    harness = Harness(default_roots(home, Path.cwd(), bundled_root(), st.extra_roots))
    rows = []
    for name, d in sorted(harness.plugins.items()):
        m = d.manifest
        status = "stopped" if m.enabled and st.plugins_enabled else "disabled"
        error = None
        try:
            validate_config(m, {**st.plugins.get(name, {}), **file_config(home, name)})
        except Exception as exc:
            status, error = "invalid_config", str(exc)
        rows.append(
            {
                "name": name,
                "version": m.version,
                "status": status,
                "provides": list(m.provides),
                "tools": [],
                "error": error,
                "verify": verify(home, name, d.path.parent),
            }
        )
    return rows


@app.command("list")
def list_(url: UrlOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List plugins with their status (live from the server when one is running)."""
    st = settings()
    rows = _api(st, url, "GET", "/api/plugins")
    live = rows is not None
    rows = rows if live else _offline_rows(st)
    if as_json:
        dump_json(rows)
        return
    table = Table(caption=None if live else "no server answered: on-disk view")
    for col in COLUMNS:
        table.add_column(col)
    for r in rows:
        table.add_row(*(", ".join(v) if isinstance(v := r.get(c), list) else str(v) for c in COLUMNS))
    console.print(table)


@app.command("add")
def add(
    path: Annotated[Path, typer.Argument(help="Local plugin folder (plugin.yaml + pyproject.toml).")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing copy.")] = False,
    url: UrlOpt = None,
) -> None:
    """Copy a local plugin folder into ~/.auoreo/plugins/<name>/ (the runtime definition root)."""
    st = settings()
    src = path.expanduser().resolve()
    try:
        loaded = load_plugin(Candidate("plugins", src.name, src / "plugin.yaml", Root("runtime", src.parent)))
    except (LoadError, OSError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    name = loaded.manifest.name
    dest = ensure_home(st.home) / "plugins" / name
    if (dest / "plugin.yaml").exists() and not force:
        err_console.print(f"[red]{dest} exists; pass --force to replace it[/red]")
        raise typer.Exit(1)
    if dest.exists():
        for child in dest.iterdir():
            if child.name != ".venv":  # keep the venv; the lock hash decides reinstalling
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(src, dest, ignore=COPY_IGNORE, dirs_exist_ok=True)
    console.print(f"added {name} {loaded.manifest.version} → {dest} (sha256 {folder_hash(dest)[:16]})")
    if _api(st, url, "POST", "/api/harness/reload") is not None:
        console.print("server reloaded; the plugin host picks it up on its next sync")


@app.command("remove")
def remove(name: str, url: UrlOpt = None) -> None:
    """Delete ~/.auoreo/plugins/<name>/ (venv included). Bundled plugins can only be disabled."""
    st = settings()
    dest = ensure_home(st.home) / "plugins" / name
    if not dest.is_dir():
        err_console.print(f"[red]no user-installed plugin '{name}' in {dest.parent}[/red]")
        raise typer.Exit(1)
    shutil.rmtree(dest)
    console.print(f"removed {dest}")
    if _api(st, url, "POST", "/api/harness/reload") is not None:
        console.print("server reloaded; the plugin is stopped on the host's next sync")


@app.command("reload")
def reload(name: str, url: UrlOpt = None) -> None:
    """Re-read the plugin from disk and restart it on the running server."""
    st = settings()
    row = _api(st, url, "POST", f"/api/plugins/{name}/reload")
    if row is None:
        err_console.print("no server answered; the plugin is (re)installed at the next server start")
        raise typer.Exit(1)
    console.print(f"{row['name']}: {row['status']}" + (f" ({row['error']})" if row.get("error") else ""))


@app.command("pair")
def pair(name: str, code: str, url: UrlOpt = None) -> None:
    """Allowlist the chat that was shown this pairing code (e.g. `auoreo plugin pair telegram 123456`)."""
    st = settings()
    res = _api(st, url, "POST", f"/api/plugins/{name}/pair", {"code": code})
    chat_id = res.get("chat_id") if res is not None else PairingStore(ensure_home(st.home), name).pair(code)
    if chat_id is None:
        err_console.print("[red]unknown or expired pairing code[/red]")
        raise typer.Exit(1)
    console.print(f"paired chat {chat_id} with {name}")
