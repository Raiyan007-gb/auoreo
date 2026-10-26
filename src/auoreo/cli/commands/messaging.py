"""H4: `auoreo messaging <platform> setup|start|stop|status|pair` (spec §18; Telegram/Discord first).

A thin layer over the plugin host (`/api/plugins/*` on a running server) and the connector
pairing store. Platforms are bundled plugins (`plugins/telegram`, `plugins/discord`) that
ship disabled; `setup` copies one into `<home>/plugins/<name>/` with `enabled: true`
(the runtime root wins over the bundled one) and checks its credentials by NAME only:
secret values are never read back to the terminal. Tokens are stored with
`auoreo secrets set <name>` (env / keyring / secrets.db, spec §7).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from auoreo.cli.commands.plugin import COPY_IGNORE, _api, _offline_rows
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness
from auoreo.plugins.connectors import PairingStore


@dataclass(frozen=True)
class Platform:
    plugin: str
    secrets: tuple[str, ...]
    """Credential names; any ONE of them is enough."""
    connector: bool
    """Inbound chats (pairing codes) as well as outbound delivery."""
    hint: str


PLATFORMS: dict[str, Platform] = {
    "telegram": Platform(
        "telegram",
        ("telegram/bot_token",),
        True,
        "message your bot; it answers unknown chats with a pairing code → "
        "`auoreo messaging telegram pair <code>`",
    ),
    "discord": Platform(
        "discord",
        ("discord/webhook_url", "discord/bot_token"),
        False,
        "outbound only: use it as a delivery target (`delivery: discord:<channel>` in jobs/webhooks)",
    ),
}
ACTIONS = ("setup", "start", "stop", "status", "pair")
UrlOpt = Annotated[
    str | None, typer.Option("--url", help="Server base URL (default: AUOREO_URL or settings).")
]


def messaging(
    platform: Annotated[str, typer.Argument(help=" | ".join(PLATFORMS))],
    action: Annotated[str, typer.Argument(help=" | ".join(ACTIONS))],
    code: Annotated[str | None, typer.Argument(help="Pairing code (for `pair`).")] = None,
    url: UrlOpt = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Set up, start, stop, inspect or pair a messaging platform (a connector plugin)."""
    p = PLATFORMS.get(platform)
    if p is None:
        err_console.print(f"[red]unknown platform '{platform}' (supported: {', '.join(PLATFORMS)})[/red]")
        raise typer.Exit(2)
    handlers = {"setup": _setup, "start": _start, "stop": _stop, "status": _status, "pair": _pair}
    handler = handlers.get(action)
    if handler is None:
        err_console.print(f"[red]unknown action '{action}' (one of {', '.join(ACTIONS)})[/red]")
        raise typer.Exit(2)
    handler(settings(), p, url, as_json, code)


# ------------------------------------------------------------------ helpers
def _harness(st: Settings) -> Harness:
    return Harness(default_roots(ensure_home(st.home), Path.cwd(), bundled_root(), st.extra_roots))


def _secret_state(st: Settings, p: Platform) -> dict[str, bool]:
    store = SecretStore(st.home, st.keyring)
    return {name: bool(store.get(name)) for name in p.secrets}


def _enable_copy(st: Settings, p: Platform, src: Path) -> tuple[Path, bool]:
    """`<home>/plugins/<name>/` with `enabled: true`; returns (dest, changed)."""
    dest = ensure_home(st.home) / "plugins" / p.plugin
    if src.resolve() != dest.resolve():
        shutil.copytree(src, dest, ignore=COPY_IGNORE, dirs_exist_ok=True)
    manifest = dest / "plugin.yaml"
    text = manifest.read_text(encoding="utf-8")
    if re.search(r"(?m)^enabled:\s*true\s*$", text):
        return dest, False
    if re.search(r"(?m)^enabled:", text):
        text = re.sub(r"(?m)^enabled:.*$", "enabled: true", text)
    else:
        text = text.rstrip("\n") + "\nenabled: true\n"
    manifest.write_text(text, encoding="utf-8")
    return dest, True


# ------------------------------------------------------------------ actions
def _setup(st: Settings, p: Platform, url: str | None, as_json: bool, _code: str | None) -> None:
    loaded = _harness(st).plugins.get(p.plugin)
    if loaded is None:
        err_console.print(f"[red]plugin '{p.plugin}' is not installed in any definition root[/red]")
        raise typer.Exit(1)
    dest, changed = _enable_copy(st, p, loaded.path.parent)
    have = _secret_state(st, p)
    reloaded = _api(st, url, "POST", "/api/harness/reload") is not None
    out = {"platform": p.plugin, "path": str(dest), "enabled": True, "secrets": have, "reloaded": reloaded}
    if as_json:
        dump_json(out)
    else:
        console.print(f"{'enabled' if changed else 'already enabled'}: {dest}")
        for name, ok in have.items():
            console.print(f"  {'✓' if ok else '·'} secret {name}" + ("" if ok else " (missing)"))
        if not st.plugins_enabled:
            console.print("[yellow]plugins_enabled is false in settings: no plugin will start[/yellow]")
        console.print(f"next: {p.hint}")
    if not any(have.values()):
        err_console.print(
            f"[yellow]no credential yet: run `auoreo secrets set {p.secrets[0]}` (value is prompted, "
            "never echoed)[/yellow]"
        )
        raise typer.Exit(1)


def _lifecycle(st: Settings, p: Platform, url: str | None, op: str) -> dict[str, Any]:
    row = _api(st, url, "POST", f"/api/plugins/{p.plugin}/{op}")
    if row is None:
        err_console.print(
            "[red]no server answered: messaging plugins run inside `auoreo serve` "
            f"(an enabled plugin starts with it). Start the server, then `{op}` again.[/red]"
        )
        raise typer.Exit(1)
    return row  # type: ignore[no-any-return]


def _start(st: Settings, p: Platform, url: str | None, as_json: bool, _code: str | None) -> None:
    row = _lifecycle(st, p, url, "start")
    dump_json(row) if as_json else console.print(f"{p.plugin}: {row['status']}" + _err(row))
    if row.get("status") != "running":
        raise typer.Exit(1)


def _stop(st: Settings, p: Platform, url: str | None, as_json: bool, _code: str | None) -> None:
    row = _lifecycle(st, p, url, "stop")
    dump_json(row) if as_json else console.print(f"{p.plugin}: {row['status']}")


def _err(row: dict[str, Any]) -> str:
    return f" ({row['error']})" if row.get("error") else ""


def _status(st: Settings, p: Platform, url: str | None, as_json: bool, _code: str | None) -> None:
    rows = _api(st, url, "GET", "/api/plugins")
    live = rows is not None
    row = next((r for r in (rows if live else _offline_rows(st)) if r["name"] == p.plugin), None)
    pairing = PairingStore(st.home, p.plugin)
    out: dict[str, Any] = {
        "platform": p.plugin,
        "installed": row is not None,
        "status": row["status"] if row else "not installed",
        "error": row.get("error") if row else None,
        "live": live,
        "secrets": _secret_state(st, p),
        "allowlist": len(pairing.allowlist()) if p.connector else None,
        "pending_pairings": len(pairing.pending()) if p.connector else None,
    }
    if as_json:
        dump_json(out)
        return
    console.print(
        f"{p.plugin}: {out['status']}{_err(out)}" + ("" if live else "  (no server answered: on-disk view)")
    )
    for name, ok in out["secrets"].items():
        console.print(f"  {'✓' if ok else '·'} secret {name}")
    if p.connector:
        console.print(
            f"  allowlisted chats: {out['allowlist']}, pending pairing codes: {out['pending_pairings']}"
        )


def _pair(st: Settings, p: Platform, url: str | None, as_json: bool, code: str | None) -> None:
    if not p.connector:
        err_console.print(f"[red]{p.plugin} has no inbound connector; nothing to pair[/red]")
        raise typer.Exit(2)
    if not code:
        err_console.print("[red]usage: auoreo messaging telegram pair <code>[/red]")
        raise typer.Exit(2)
    res = _api(st, url, "POST", f"/api/plugins/{p.plugin}/pair", {"code": code})
    chat_id = (
        res.get("chat_id") if res is not None else PairingStore(ensure_home(st.home), p.plugin).pair(code)
    )
    if chat_id is None:
        err_console.print("[red]unknown or expired pairing code[/red]")
        raise typer.Exit(1)
    dump_json({"paired": True, "chat_id": chat_id}) if as_json else console.print(f"paired chat {chat_id}")
