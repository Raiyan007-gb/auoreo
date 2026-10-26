"""Diagnostics-back for editing tools (spec §9.3 `apply_patch`: verify → apply → LSP
diagnostics). Fail-open: no language server, a slow one, or any LSP error just means no
diagnostics — never a failed edit.

`settings.tools.apply_patch_lsp`: `running` (default) only asks servers this session has
already started (e.g. through the `lsp` tool), `start` may start one, `off` never asks.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from auoreo.tools.context import ToolContext
from auoreo.tools.lsp_client import EXT_LANG, LspClient, find_root, manager_for, path_to_uri

SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}
MAX_ROWS = 30


async def _client(ctx: ToolContext, svc: Any, path: Path, mode: str) -> LspClient | None:
    language = EXT_LANG[path.suffix][0]
    cwd = ctx.cwd.resolve()
    root = find_root(path, language, cwd if path.is_relative_to(cwd) else Path(path.anchor))
    if root is None:
        return None
    mgr = manager_for(svc)
    if mode == "running":
        client = mgr.clients.get((ctx.session_id, language, str(root)))
        return client if client is not None and client.alive else None
    from auoreo.core.secrets import child_env

    return await mgr.client_for(ctx.session_id, language, root, ctx.env or child_env([], None))


async def _items(client: LspClient, path: Path, timeout_s: float) -> list[dict[str, Any]]:
    uri = path_to_uri(path)
    if client.capabilities.get("diagnosticProvider"):
        await client.open(path, EXT_LANG[path.suffix][1])
        report = await client.request(
            "textDocument/diagnostic", {"textDocument": {"uri": uri}}, timeout_s=timeout_s
        )
        return list((report or {}).get("items") or [])
    client.diagnostics.pop(uri, None)  # drop the pre-edit publish; wait for a fresh one
    client._diag_events.pop(uri, None)
    await client.open(path, EXT_LANG[path.suffix][1])
    return await client.wait_diagnostics(uri, timeout_s)


async def diagnostics_for(ctx: ToolContext, paths: list[Path]) -> list[str]:
    """`path:line:col severity: message` rows (errors and warnings) for the edited files."""
    svc = ctx.services.get("services")
    cfg = getattr(getattr(svc, "settings", None), "tools", None)
    mode = str(getattr(cfg, "apply_patch_lsp", "running"))
    timeout_s = float(getattr(cfg, "apply_patch_lsp_timeout_s", 3.0))
    if svc is None or mode == "off":
        return []
    rows: list[str] = []
    for path in paths:
        if path.suffix not in EXT_LANG or not path.is_file():
            continue
        try:
            async with asyncio.timeout(timeout_s * 2):
                client = await _client(ctx, svc, path, mode)
                if client is None:
                    continue
                items = await _items(client, path, timeout_s)
        except Exception:  # fail-open: diagnostics are advisory
            continue
        rel = path.relative_to(ctx.cwd.resolve()) if path.is_relative_to(ctx.cwd.resolve()) else path
        for d in items:
            sev = int(d.get("severity") or 1)
            if sev > 2:
                continue
            start = (d.get("range") or {}).get("start") or {}
            msg = " ".join(str(d.get("message", "")).split())[:300]
            code = f" [{d['code']}]" if d.get("code") is not None else ""
            line, col = int(start.get("line", 0)) + 1, int(start.get("character", 0)) + 1
            rows.append(f"{rel}:{line}:{col} {SEVERITY[sev]}: {msg}{code}")
    with contextlib.suppress(Exception):
        rows.sort(key=lambda r: (" error:" not in r, r))
    return rows[:MAX_ROWS] + ([f"… {len(rows) - MAX_ROWS} more"] if len(rows) > MAX_ROWS else [])
