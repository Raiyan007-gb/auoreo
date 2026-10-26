"""I5: `POST /api/harness/reload?kind=` — scoped reloads behind `auoreo <kind> reload`.

Every kind re-discovers the definition roots (cheap, atomic, a failure keeps the previous
snapshot); the kind decides what else happens and which part of the diff is reported:

| kind      | extra effect                                                      | diff reported      |
|-----------|-------------------------------------------------------------------|--------------------|
| `agent`   | —                                                                 | `agents`           |
| `command` | —                                                                 | `commands`         |
| `mcp`     | MCP bridge re-syncs servers (connect new, drop removed)          | `mcp`              |
| `lsp`     | in-process language servers stop (they restart on next use)      | `lsp.stopped`      |
| `config`  | settings.json re-read + validated; changed keys listed           | `config.changed`   |
| `model`   | same as `config`, limited to model routing keys                  | `config.changed`   |

Settings are NOT hot-swapped into the running services (that would race in-flight turns):
`config`/`model` report what changed and say when it applies, honestly.
"""

from __future__ import annotations

from typing import Any, Literal

ReloadKind = Literal["agent", "command", "model", "config", "lsp", "mcp"]
MODEL_KEYS = ("model", "small_model", "models", "aliases", "fallbacks", "auxiliary", "gateway_tuning")
_DIFF_KEY: dict[str, str] = {"agent": "agents", "command": "commands", "mcp": "mcp"}


def _changed_settings(svc: Any, keys: tuple[str, ...] | None) -> list[str]:
    from auoreo.core.settings import Settings, get_settings

    get_settings.cache_clear()
    fresh = Settings(home=svc.settings.home)  # raises ValidationError on a broken settings.json
    old, new = svc.settings.model_dump(mode="json"), fresh.model_dump(mode="json")
    names = keys or tuple(sorted({*old, *new}))
    return [k for k in names if old.get(k) != new.get(k) and k != "home"]


async def scoped_reload(
    svc: Any, kind: ReloadKind, diff: dict[str, Any]
) -> tuple[dict[str, dict[str, list[str]]], str | None]:
    """`(changes to report, note)` after the harness reload already produced `diff`."""
    if kind in _DIFF_KEY:
        key = _DIFF_KEY[kind]
        if kind == "mcp" and svc.mcp is not None:
            await svc.mcp.sync()
        return {key: diff.get(key, {"added": [], "removed": []})}, None
    if kind == "lsp":
        from auoreo.tools.lsp_client import manager_for

        stopped = await stop_lsp(manager_for(svc))
        return {"lsp": {"stopped": stopped}}, "language servers restart on their next use"
    changed = _changed_settings(svc, MODEL_KEYS if kind == "model" else None)
    note = (
        "settings.json is valid; changed keys apply at the next server start"
        if changed
        else "settings.json is valid; nothing changed"
    )
    return {"config": {"changed": changed}}, note


async def stop_lsp(mgr: Any, session_id: str | None = None, language: str | None = None) -> list[str]:
    """Stop in-process language servers (all, or those of one session/language).
    Returns `session:language:root` labels of what was stopped."""
    out: list[str] = []
    for key in list(mgr.clients):
        sid, lang, root = key
        if session_id not in (None, sid) or language not in (None, lang):
            continue
        client = mgr.clients.pop(key)
        await client.close()
        out.append(f"{sid}:{lang}:{root}")
    return out
