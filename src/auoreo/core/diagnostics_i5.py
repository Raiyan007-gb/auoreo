"""I5 doctor rows (spec §20.3 "disk/SSE-connections, git worktree, desktop/TCC, config schema").

- `disk`: free space on the home's filesystem (warn/error thresholds in `i5.doctor`).
- `settings_schema`: `<home>/settings.json` parses, validates against `Settings`, and has no
  unknown top-level keys (they would be silently ignored).
- `worktrees`: feature worktrees under `.auoreo/worktrees` and how many are stale/prunable.
- `desktop_tcc`: macOS Screen Recording + Accessibility grants for desktop control (skipped
  off macOS).
- `sse_connections` (CLI only, live server): open `/api/events` streams from `/metrics`.

Each probe that does not apply says `skipped: <why>` instead of pretending to have checked.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from auoreo.core.diagnostics import Check
from auoreo.core.settings import Settings

GB = 1024**3


def disk_row(st: Settings) -> Check:
    cfg = st.i5.doctor
    probe = st.home if st.home.exists() else st.home.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return Check("disk", "warn", f"cannot stat {probe}: {exc}")
    free = usage.free / GB
    detail = f"{free:.1f} GiB free of {usage.total / GB:.1f} GiB at {probe}"
    if free < cfg.disk_error_free_gb:
        return Check("disk", "error", detail, "free disk space (sessions, logs and state.db grow here)")
    if free < cfg.disk_warn_free_gb:
        return Check("disk", "warn", detail, "free disk space or move AUOREO_HOME to a larger volume")
    return Check("disk", "ok", detail)


def settings_schema_row(st: Settings) -> Check:
    from auoreo.core import settings_store

    try:
        data = settings_store.read(st.home)
    except settings_store.SettingsStoreError as exc:
        return Check("settings_schema", "error", str(exc), "check state.db (`auoreo db`)")
    if not data:
        return Check("settings_schema", "ok", "no stored settings (defaults)")
    from pydantic import ValidationError

    try:
        Settings.model_validate({**data, "home": st.home})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return Check(
            "settings_schema",
            "error",
            f"{exc.error_count()} invalid value(s); first at {where}: {first['msg']}",
            "`auoreo config edit` and fix the value",
        )
    unknown = sorted(set(data) - set(Settings.model_fields))
    if unknown:
        return Check(
            "settings_schema",
            "warn",
            f"unknown top-level key(s) ignored: {', '.join(unknown[:8])}",
            "remove or rename them (typo?)",
        )
    return Check("settings_schema", "ok", f"stored settings valid ({len(data)} top-level keys)")


async def worktrees_row(st: Settings, cwd: Path | None = None) -> Check:
    from auoreo.core.doctor_fix import worktree_root
    from auoreo.runtime import worktrees as wt
    from auoreo.runtime.subtask import WorktreeError

    root = worktree_root(st)
    try:
        _top, items = await wt.plan_gc(cwd or Path.cwd(), root, st.i5.doctor.worktree_stale_days)
    except WorktreeError as exc:
        return Check("worktrees", "ok", f"skipped: {str(exc)[:120]}")
    if not items:
        return Check("worktrees", "ok", "no feature worktrees")
    stale = [i for i in items if i.action != "keep"]
    detail = f"{len(items)} worktree(s), {len(stale)} stale/prunable"
    if stale:
        return Check("worktrees", "warn", detail, "`auoreo doctor --fix` (or `auoreo worktree gc`)")
    return Check("worktrees", "ok", detail)


def _mac_grant(lib: str, symbol: str) -> bool | None:
    import ctypes
    import ctypes.util

    try:
        handle = ctypes.cdll.LoadLibrary(ctypes.util.find_library(lib) or lib)
        fn = getattr(handle, symbol)
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_bool
    return bool(fn())


def tcc_row(platform: str | None = None) -> Check:
    if (platform or sys.platform) != "darwin":
        return Check("desktop_tcc", "ok", "skipped: not macOS (TCC grants are macOS only)")
    screen = _mac_grant("CoreGraphics", "CGPreflightScreenCaptureAccess")
    access = _mac_grant("ApplicationServices", "AXIsProcessTrusted")
    missing = [n for n, ok in (("Screen Recording", screen), ("Accessibility", access)) if ok is False]
    unknown = [n for n, ok in (("Screen Recording", screen), ("Accessibility", access)) if ok is None]
    if missing:
        return Check(
            "desktop_tcc",
            "warn",
            f"not granted to this process: {', '.join(missing)}",
            "System Settings → Privacy & Security → grant the terminal/desktop app, then restart it",
        )
    if unknown:
        return Check("desktop_tcc", "warn", f"could not query: {', '.join(unknown)}")
    return Check("desktop_tcc", "ok", "Screen Recording and Accessibility granted")


async def i5_checks(st: Settings) -> list[Check]:
    """Registered into `diagnostics.EXTRA_CHECKS` (API and CLI doctor)."""
    return [disk_row(st), settings_schema_row(st), await worktrees_row(st), tcc_row()]


def _metric(text: str, name: str) -> float | None:
    total: float | None = None
    for line in text.splitlines():
        if line.startswith(name) and not line.startswith("#"):
            try:
                total = (total or 0.0) + float(line.rsplit(" ", 1)[1])
            except (IndexError, ValueError):
                continue
    return total


async def sse_row(st: Settings, base: str, health: dict[str, Any] | None) -> Check:
    """CLI only: the live server's open `/api/events` streams (its `/metrics`)."""
    if health is None:
        return Check("sse_connections", "ok", f"skipped: no auoreo server answering on {base}")
    headers = {"Authorization": f"Bearer {st.server_password}"} if st.server_password else {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(base + "/metrics", headers=headers)
    except httpx.HTTPError as exc:
        return Check("sse_connections", "warn", f"/metrics unreachable ({type(exc).__name__})")
    if r.status_code != 200:
        return Check("sse_connections", "warn", f"/metrics answered {r.status_code}", "check the password")
    n = _metric(r.text, "active_sse_connections")
    return Check("sse_connections", "ok", f"{int(n or 0)} open /api/events stream(s) on {base}")
