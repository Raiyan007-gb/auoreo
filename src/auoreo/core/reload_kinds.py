"""I5: `POST /api/harness/reload?kind=` — scoped reloads behind `auoreo <kind> reload`.

Every kind re-discovers the definition roots (cheap, atomic, a failure keeps the previous
snapshot); the kind decides what else happens and which part of the diff is reported:

| kind      | extra effect                                                      | diff reported      |
|-----------|-------------------------------------------------------------------|--------------------|
| `agent`   | —                                                                 | `agents`           |
| `command` | —                                                                 | `commands`         |
| `mcp`     | MCP bridge re-syncs servers (connect new, drop removed)          | `mcp`              |
| `lsp`     | in-process language servers stop (they restart on next use)      | `lsp.stopped`      |
| `config`  | settings.json re-read, validated and applied live (`live_settings`) | `config.changed` |
| `model`   | same as `config`, reporting only model routing keys              | `config.changed`   |

`config`/`model` go through `core.live_settings`: live keys are copied into the running
settings in place (a step already in flight keeps its frozen request), startup-only keys are
reported as needing a restart — honestly, per key.
"""

from __future__ import annotations

from typing import Any, Literal

ReloadKind = Literal["agent", "command", "model", "config", "lsp", "mcp"]
MODEL_KEYS = ("model", "small_model", "models", "aliases", "fallbacks", "auxiliary", "gateway_tuning")
_DIFF_KEY: dict[str, str] = {"agent": "agents", "command": "commands", "mcp": "mcp"}


async def _apply_settings(svc: Any, keys: tuple[str, ...] | None) -> tuple[list[str], list[str]]:
    """`(changed, restart_required)` after applying settings.json live (ValidationError when broken)."""
    from auoreo.core.live_settings import live_of

    res = await live_of(svc).reload("reload")

    def pick(names: list[str]) -> list[str]:
        return [k for k in names if keys is None or k in keys]

    return pick(res.changed), pick(res.restart_required)


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
    changed, restart = await _apply_settings(svc, MODEL_KEYS if kind == "model" else None)
    note = "settings.json is valid; nothing changed"
    if changed:
        note = "settings.json is valid; changes applied live"
    if restart:
        note += "; restart required for: " + ", ".join(restart)
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
