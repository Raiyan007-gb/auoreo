"""What the web sees of the environment: the catalog with effective values, sources and
whether each entry is writable from the browser (`/api/env`).

Writability is one policy (`EnvPolicy`): pass-through names and `[env-only]` security knobs are
read-only (env-only editing is a future step-up flag, off by default), bootstrap fields (home,
db_encryption, host, port, workers) are env/CLI-only because they are needed before state.db
opens, and a value that comes from the real environment (or a startup flag) cannot be overridden
from here. Secret values never leave the store: entries report `is_set` and `updated_at` only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from auoreo.core import env_store
from auoreo.core.env_catalog import WEB_MANAGED, EnvVar, catalog
from auoreo.core.redaction import redact_obj
from auoreo.core.settings_store import BOOTSTRAP

LOCK_REASONS: dict[str, str] = {
    "passthrough": "read from the process environment only (export it before starting the server)",
    "env-only": "security knob: set in the real environment (export …) and restart",
    "env": "set in the real environment, which always wins: unset it there to manage it here",
    "override": "set by a command-line flag when the server started",
    "server_password": "the server password is set in the real environment (AUOREO_SERVER_PASSWORD)",
    "bootstrap": "bootstrap setting: {why}; set it in the real environment or a command-line flag",
}


@dataclass(frozen=True)
class EnvPolicy:
    env_only_editable: bool = False
    """Step-up hook: allow `[env-only]` knobs from the web (never on by default)."""


POLICY = EnvPolicy()


@dataclass(frozen=True)
class Lock:
    reason: str
    status: int
    """HTTP status a write gets: 403 (never from the web) or 409 (shadowed by another source)."""


def lock_of(e: EnvVar, source: str, policy: EnvPolicy = POLICY) -> Lock | None:
    """Why the web may not write `e` right now (None = writable)."""
    if e.kind == "passthrough":
        return Lock(LOCK_REASONS["passthrough"], 403)
    if e.tier == "env-only" and not policy.env_only_editable:
        return Lock(LOCK_REASONS["env-only"], 403)
    if e.field in WEB_MANAGED:
        return Lock(WEB_MANAGED[e.field], 403)
    if e.field == "server_password":
        return Lock(LOCK_REASONS["server_password"], 403)
    if e.field in BOOTSTRAP:
        return Lock(LOCK_REASONS["bootstrap"].format(why=BOOTSTRAP[e.field]), 403)
    if source in ("env", "override"):
        return Lock(LOCK_REASONS[source], 409)
    return None


def _ts(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, UTC) if epoch else None


def entry_view(
    e: EnvVar,
    svc: Any,
    file_data: dict[str, Any],
    overrides: dict[str, Any],
    stored: set[str],
    updated: dict[str, int],
    running: dict[str, Any],
    built: dict[str, Any] | None,
) -> dict[str, Any]:
    if e.kind == "setting" and e.field:
        source = env_store.setting_source(e.name, e.field, file_data, overrides)
        raw = running.get(e.field)
        value = redact_obj(raw)
        is_set = source != "default"
        pending = built is not None and e.field in built and built[e.field] != raw and not e.live
    else:
        src = env_store.env_source(e.name)
        in_store = e.name in stored
        source = src or ("secrets" if in_store else "default")
        is_set = bool(src or in_store) or (
            e.field == "server_password" and bool(svc.settings.server_password)
        )
        value = None
        if e.kind == "passthrough" and e.field:
            value = redact_obj(str(running.get(e.field) or ""))
        elif e.kind == "passthrough" and src:
            value = redact_obj(os.environ.get(e.name, ""))
        pending = False
    lock = lock_of(e, source)
    return {
        "name": e.name,
        "field": e.field,
        "description": e.description,
        "kind": e.kind,
        "type": e.type,
        "choices": list(e.choices),
        "nullable": e.nullable,
        "tier": e.tier,
        "source": source,
        "value": value,
        "default": None if e.kind != "setting" else redact_obj(e.default),
        "is_set": is_set,
        "updated_at": _ts(updated.get(e.name)) if e.kind == "secret" else None,
        "live": e.live,
        "restart_reason": e.restart_reason,
        "pending_restart": bool(pending),
        "writable": lock is None,
        "locked_reason": lock.reason if lock else None,
    }


def listing(svc: Any, live: Any, stored: set[str], updated: dict[str, int]) -> dict[str, Any]:
    from auoreo.core.env_custom import custom

    try:
        file_data = env_store.read(svc.settings.home)
    except env_store.EnvStoreError:
        file_data = {}
    try:
        built = live.build().model_dump(mode="json")
    except Exception:  # a broken store: show running values, nothing pending
        built = None
    sections, entries = catalog()
    rows: dict[str, list[dict[str, Any]]] = {s.id: [] for s in sections}
    running = svc.settings.model_dump(mode="json")
    for e in entries:
        view = entry_view(e, svc, file_data, live.overrides, stored, updated, running, built)
        rows[e.section].append(view)
    out_custom = [
        {
            "name": name,
            "secret": var.secret,
            "expose_to_shell": var.expose_to_shell,
            "expose_to_agent": var.expose_to_agent,
            "value": None if var.secret else redact_obj(var.value or ""),
            "is_set": (name in stored) if var.secret else True,
            "updated_at": var.updated_at,
        }
        for name, var in sorted(custom(svc.settings).items())
    ]
    from auoreo.core.env_import import dotenv_files

    files = dotenv_files(svc)
    return {
        "sections": [{"id": s.id, "label": s.label, "entries": rows[s.id]} for s in sections],
        "custom": out_custom,
        "pending_restart": list(getattr(live, "pending", [])),
        "history": env_store.history(svc.settings.home),
        "dotenv_path": str(files[0]) if files else str(svc.settings.home / ".env"),
        "dotenv_paths": [str(p) for p in files],
        "dotenv_exists": bool(files),
    }


__all__ = ["POLICY", "EnvPolicy", "Lock", "entry_view", "listing", "lock_of"]
