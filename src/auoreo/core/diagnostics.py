"""Environment diagnostics shared by `auoreo doctor` and `GET /api/doctor`.

Every check returns `list[Check]`; nothing here repairs anything and no check ever puts a
secret value in its detail (credential rows name env vars, never their contents).

`EXTRA_CHECKS` is the extension point: another subsystem appends an
`async def check(settings) -> list[Check]` and its rows appear after the built-in ones.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from auoreo.core import sandbox_boot
from auoreo.core.paths import bundled_root
from auoreo.core.settings import ModelGroup, Settings
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness, HarnessError

if TYPE_CHECKING:
    from auoreo.core.services import Services

Level = Literal["ok", "warn", "error"]


@dataclass(frozen=True)
class Check:
    check: str
    level: Level
    detail: str
    remedy: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


ExtraCheck = Callable[[Settings], Awaitable[list[Check]]]
EXTRA_CHECKS: list[ExtraCheck] = []
"""Rows contributed by optional subsystems (e.g. OpenTelemetry); run after the built-ins.
A check that raises becomes one `warn` row instead of failing the whole report."""


# ------------------------------------------------------------------ runtime
def python_checks() -> list[Check]:
    py = sys.version_info
    return [
        Check("python", "ok" if py >= (3, 12) else "error", sys.version.split()[0], "install Python 3.12+")
    ]


def sqlite_checks() -> list[Check]:
    sv = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    return [
        Check(
            "sqlite",
            "ok" if sv >= (3, 41) else "warn",
            sqlite3.sqlite_version,
            "SQLite ≥3.41 is needed for LIMIT-style vector KNN",
        )
    ]


def vec_checks() -> list[Check]:
    try:
        import sqlite_vec

        db = sqlite3.connect(":memory:")
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        return [Check("sqlite-vec", "ok", db.execute("select vec_version()").fetchone()[0])]
    except Exception as exc:
        return [
            Check(
                "sqlite-vec",
                "warn",
                f"unavailable ({exc})",
                "memory runs FTS-only; use a Python build with loadable extensions",
            )
        ]


def home_checks(st: Settings) -> list[Check]:
    try:
        st.home.mkdir(parents=True, exist_ok=True)
        probe = st.home / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        return [Check("home", "ok", str(st.home))]
    except OSError as exc:
        return [Check("home", "error", f"{st.home}: {exc}", "set AUOREO_HOME to a writable dir")]


# ------------------------------------------------------------------ harness
def sandbox_detail(st: Settings, inst: object, ok: bool, detail: str) -> tuple[Level, str]:
    """The sandbox row says whether isolation is enforced and what the policy does on failure."""
    caps: dict[str, bool] = getattr(inst, "capabilities", dict)()
    enforced = ok and any(caps.values())
    detail = f"{detail}; enforced={enforced}; policy={st.sandbox_policy}"
    if ok and not enforced and getattr(inst, "name", "") == "none":
        return "warn", detail + " → no isolation: install bubblewrap (Linux) or set backends.sandbox"
    if ok:
        return "ok", detail
    if st.sandbox_policy == "fail":
        return "error", detail + " → the server refuses to start"
    return "warn", detail + " → falls back to 'none' (enforced=False)"


async def backend_checks(st: Settings, h: Harness, services: Services | None = None) -> list[Check]:
    """One row per backend kind: which one is selected and whether its `check()` passes.
    With live `services` the cached instance is reused instead of constructing a new one."""
    out: list[Check] = []
    kinds = sorted({d.manifest.kind for d in h.snapshot.backends.values()})
    for kind in kinds:
        try:
            chosen = sandbox_boot.configured(st, h) if kind == "sandbox" else st.backends.get(kind)
            bdef = h.select_backend(kind, chosen)
        except KeyError as exc:
            out.append(Check(f"backend:{kind}", "error", str(exc), "fix settings.backends"))
            continue
        if bdef is None:
            names = sorted(h.backends_for(kind))
            out.append(
                Check(
                    f"backend:{kind}",
                    "warn",
                    f"none selected (available: {names})",
                    f"set settings.backends.{kind}",
                )
            )
            continue
        m = bdef.manifest
        missing = [b for b in m.requires_binaries if shutil.which(b) is None]
        posture = "" if m.posture == "ok" else f" [{m.posture}]"
        if missing:
            out.append(
                Check(
                    f"backend:{kind}",
                    "warn",
                    f"{m.name}{posture}: missing {missing}",
                    f"install {', '.join(missing)} or pick another backend",
                )
            )
            continue
        try:
            inst = services.backend(kind) if services is not None else bdef.load()(st)
            status = await asyncio.wait_for(inst.check(), 10)
            ok = bool(getattr(status, "ok", status))
            detail = getattr(status, "detail", "") or ("ready" if ok else "not ready")
            remedy = getattr(status, "remedy", "") or ""
            level: Level = "ok" if ok else "warn"
            if kind == "sandbox":
                level, detail = sandbox_detail(st, inst, ok, detail)
            out.append(Check(f"backend:{kind}", level, f"{m.name}{posture}: {detail}", remedy))
        except Exception as exc:
            out.append(
                Check(f"backend:{kind}", "warn", f"{m.name}{posture}: {exc}", "see backends/README.md")
            )
    return out


def plugin_checks(st: Settings, h: Harness) -> list[Check]:
    """One row per enabled plugin: is its venv/lock still in sync with the folder?"""
    from auoreo.plugins.install import verify

    out: list[Check] = []
    for name, d in sorted(h.plugins.items()):
        if not d.manifest.enabled:
            continue
        state = verify(st.home, name, d.path.parent)
        level: Level = "ok" if state == "ok" else "warn"
        out.append(
            Check(
                f"plugin:{name}", level, state, "" if state == "ok" else f"run `auoreo plugin reload {name}`"
            )
        )
    return out


async def harness_checks(st: Settings, services: Services | None = None) -> list[Check]:
    """Backends, plugins and the definition summary (loaded fresh unless services are live)."""
    try:
        h = (
            services.harness
            if services is not None
            else Harness(default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots))
        )
    except HarnessError as exc:
        return [
            Check(
                "harness",
                "error",
                f"{len(exc.errors)} bundled error(s): {exc.errors[0]}",
                "fix the bundled definition",
            )
        ]
    level: Level = "warn" if h.errors else "ok"
    counts = {k: len(v) for k, v in h.describe().items()}
    out = await backend_checks(st, h, services)
    out.extend(plugin_checks(st, h))
    out.append(
        Check(
            "harness", level, f"{counts}; {len(h.errors)} overlay error(s)", "run `auoreo harness validate`"
        )
    )
    return out


# ------------------------------------------------------------------ credentials & server
def credential_checks(st: Settings, secret: Callable[[str], str | None] | None = None) -> list[Check]:
    """One row per configured model: the provider prefix decides which key it needs.
    Details name env vars only; a key's value is never included."""
    if st.gateway != "litellm":
        return [Check("credentials", "ok", "fake gateway (no key needed)")]
    from auoreo.gateway.gateway import credential_status

    if secret is None:
        from auoreo.core.secrets import SecretStore

        secret = SecretStore(st.home, st.keyring).get
    models = [m for g in ("default", "small") for m in st.models.get(g, ModelGroup()).models]
    if st.model and "/" in st.model:
        models.insert(0, st.model)
    rows: list[Check] = []
    for model in dict.fromkeys(models):
        ready, detail = credential_status(model, secret)
        if ready:
            rows.append(Check("credentials", "ok", detail))
            continue
        key = detail.split("missing ", 1)[-1].split(" or ")[0].split(",")[0]
        hint = f"set {key} in .env, the environment, or `auoreo secrets set {key}`"
        rows.append(Check("credentials", "warn", detail, hint))
    return rows


def port_checks(st: Settings, *, serving: bool = False) -> list[Check]:
    """CLI: is the port free? From inside the server the port is ours, so it is reported as such."""
    if serving:
        return [Check("port", "ok", f"{st.host}:{st.port} serving (this process)")]
    with socket.socket() as s:
        free = s.connect_ex((st.host, st.port)) != 0
    return [
        Check(
            "port",
            "ok" if free else "warn",
            f"{st.host}:{st.port} {'free' if free else 'in use'}",
            "another process is listening; pick --port or stop it",
        )
    ]


def auth_checks(st: Settings) -> list[Check]:
    return [
        Check(
            "auth",
            "ok" if st.server_password or st.is_loopback else "error",
            "password set" if st.server_password else "no password (loopback only)",
            "set AUOREO_SERVER_PASSWORD before binding beyond loopback",
        )
    ]


def _audit_row(broken: int | None) -> Check:
    return Check(
        "audit",
        "ok" if broken is None else "error",
        "hash chain intact" if broken is None else f"chain broken at seq {broken}",
        "the audit log was modified outside auoreo",
    )


async def audit_checks(st: Settings, services: Services | None = None) -> list[Check]:
    """Recompute the audit hash chain (live log when serving; skipped if no database yet)."""
    if services is not None:
        return [_audit_row(await services.audit.verify())]
    if not st.db_path.is_file():
        return []
    from auoreo.core.audit import AuditLog
    from auoreo.core.db import Database

    db = Database(st.db_path)
    await db.init()
    try:
        return [_audit_row(await AuditLog(db).verify())]
    finally:
        await db.close()


def yolo_checks() -> list[Check]:
    on = bool(os.environ.get("AUOREO_YOLO"))
    return [
        Check(
            "yolo",
            "warn" if on else "ok",
            "AUOREO_YOLO set (prompts skipped; floors still apply)" if on else "off",
        )
    ]


def observability_checks(st: Settings) -> list[Check]:
    from auoreo.core import observability

    level, detail = observability.status(st)
    return [Check("otel", "ok" if level == "ok" else "warn", detail)]


async def extra_checks(st: Settings) -> list[Check]:
    out: list[Check] = []
    for fn in list(EXTRA_CHECKS):
        try:
            out.extend(await fn(st))
        except Exception as exc:
            name = getattr(fn, "__name__", "extra")
            out.append(Check(f"extra:{name}", "warn", f"check failed: {exc}"))
    return out


# ------------------------------------------------------------------ entry point
async def run_checks(settings: Settings, *, services: Services | None = None) -> list[Check]:
    """Every check in display order. `services` (the API) reuses the live harness, secret store,
    backends and audit log; without it (the CLI) each check opens what it needs."""
    secret: Any = services.secrets.get if services is not None else None
    out: list[Check] = [
        *python_checks(),
        *sqlite_checks(),
        *vec_checks(),
        *home_checks(settings),
        *await harness_checks(settings, services),
        *credential_checks(settings, secret),
        *port_checks(settings, serving=services is not None),
        *auth_checks(settings),
        *await audit_checks(settings, services),
        *yolo_checks(),
        *observability_checks(settings),
        *await extra_checks(settings),
    ]
    # --- W1-A: exec policies (loaded count + compile errors; a broken policy denies everything)
    try:
        from auoreo.core.policy import policy_engine

        ph = (
            services.harness
            if services is not None
            else Harness(default_roots(settings.home, Path.cwd(), bundled_root(), settings.extra_roots))
        )
        pe = policy_engine(ph)
        live = [p.name for p in pe.policies if p.broken is None]
        detail = f"{len(live)} loaded ({', '.join(live) or 'none'}); {len(pe.errors)} compile error(s)"
        if pe.errors:
            detail += f": {pe.errors[0]}"
        fix = "fix the policy (it denies every request until then)" if pe.errors else ""
        at = next((i + 1 for i, c in enumerate(out) if c.check == "harness"), len(out))
        out.insert(at, Check("policies", "error" if pe.errors else "ok", detail, fix))
    except HarnessError:
        pass  # already reported by the harness row
    # --- end W1-A
    # --- W1-E: data at rest (db_encryption, sqlcipher) + workers/redis (2 s ping)
    from auoreo.core import bus_redis, crypto_columns

    w1e: list[tuple[str, Awaitable[list[Check]]]] = [
        ("db_encryption", crypto_columns.doctor_checks(settings, services)),
        ("workers", bus_redis.doctor_checks(settings)),
    ]
    rows: list[Check] = []
    for name, pending in w1e:
        try:
            rows.extend(await pending)
        except Exception as exc:
            rows.append(Check(name, "warn", f"check failed: {exc}"))
    at = next((i for i, c in enumerate(out) if c.check == "yolo"), len(out))  # right after `audit`
    out[at:at] = rows
    # --- end W1-E
    # --- W2-A: named execution environments (settings.environments), each with its check()
    if settings.environments:
        from auoreo.tools.environments import describe

        eh = services.harness if services is not None else None
        try:
            eh = eh or Harness(default_roots(settings.home, Path.cwd(), bundled_root(), settings.extra_roots))
            for name, ok, detail, fix in await describe(settings, eh):
                out.append(Check(f"environment:{name}", "ok" if ok else "warn", detail, "" if ok else fix))
        except HarnessError:
            pass  # already reported by the harness row
    # --- end W2-A
    # --- W3-A: where the bundled definitions and the SPA were found, and how this is installed
    from auoreo.core.paths import web_dist_dir
    from auoreo.core.upgrade import install_method

    dist = web_dist_dir()
    has_ui = (dist / "index.html").is_file()
    at = next((i + 1 for i, c in enumerate(out) if c.check == "home"), len(out))
    out[at:at] = [
        Check("install", "ok", f"{install_method()} · bundled root {bundled_root()}"),
        Check(
            "web_dist",
            "ok" if has_ui else "warn",
            str(dist) if has_ui else f"{dist} missing (API-only, no UI at /)",
            "cd web && npm ci && npm run build",
        ),
    ]
    # --- end W3-A
    # --- F6: memory.db cipher state (sqlcipher mode covers memory.db; columns mode does not)
    from auoreo.core import sqlcipher

    try:
        lvl, detail, fix = sqlcipher.memory_status(settings.home, settings.db_encryption)
        row = Check("memory_db_cipher", lvl, detail, fix)
    except Exception as exc:
        row = Check("memory_db_cipher", "warn", f"check failed: {exc}")
    at = next((i + 1 for i, c in enumerate(out) if c.check == "db_encryption"), len(out))
    out.insert(at, row)
    # --- end F6
    # --- G1a: enabled webhook routes whose secret is missing (they answer 403)
    import contextlib

    from auoreo.automation.webhook_verify import doctor_rows

    with contextlib.suppress(HarnessError):  # a broken harness is already reported above
        out.extend(Check(*row) for row in doctor_rows(settings, services))
    # --- end G1a
    return out


def summarize(checks: list[Check]) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "error": 0}
    for c in checks:
        counts[c.level] += 1
    return counts


# --- W2-D: teleop / ICE rows live in diagnostics_rtc (kept separate for the 500-line cap)
from auoreo.core.diagnostics_rtc import (  # noqa: E402
    STUN_TIMEOUT_S,
    rtc_checks,
    stun_binding,
    stun_target,
)

EXTRA_CHECKS.append(rtc_checks)
__all__ = ["STUN_TIMEOUT_S", "stun_binding", "stun_target"]
from auoreo.core.diagnostics_h1b import h1b_checks  # noqa: E402  --- H1b: keyring/ws/plugin quarantine

EXTRA_CHECKS.append(h1b_checks)
