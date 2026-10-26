"""Plugin trust tiers (spec §25.15 L1076/L1077/L1081, §26.14 L1296).

    bundled (signed) > allow-listed (pinned) > local-path (--trust-local) > URL (deny)

Every install records provenance in `<home>/plugins/<name>/lock.json`:
`{registry, version, sha256, signed_by, tier}` (+ `venv_sha256`, and for pip installs
`package: {name, version, sha256}` and `entry`). Gates:

- before start: a bundled plugin must match a signature by a pinned key
  (`i6.plugins.bundled_signatures`: enforce | warn | off); an allow-listed install must still
  be allow-listed (pin unchanged, or its tap still configured); URL sources never run.
- after install: the venv is re-hashed (every `*.dist-info/RECORD` + each recorded file's
  sha256) against `venv_sha256`; a mismatch quarantines the plugin.

`importlib.metadata.entry_points(group="auoreo.plugin")` is enumerated WITHOUT importing:
packages found that way are only listed (status `quarantined`) until `plugin pip-add`
installs them into their own venv and they pass verify.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata as md
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from auoreo.plugins import install as inst
from auoreo.plugins.signing import read_signatures, verify_entry

if TYPE_CHECKING:
    from auoreo.core.settings import Settings
    from auoreo.plugins.host import PluginHost, PluginState

Tier = Literal["bundled", "allow-listed", "local-path", "url"]
TIER_RANK: dict[str, int] = {"bundled": 0, "allow-listed": 1, "local-path": 2, "url": 3}
MAX_RECORD_FILES = 20_000


def classify(source: str) -> Tier:
    """Tier of an `auoreo plugin add` source string (a path or a URL)."""
    return "url" if "://" in source or source.startswith("git@") else "local-path"


def admit(tier: str, *, trust_local: bool) -> str | None:
    """Refusal reason for adding a source of `tier`, None when it may be added (quarantined)."""
    if tier == "url":
        return "URL plugin sources are denied; clone it, review it and add the local folder"
    if tier == "local-path" and not trust_local:
        return "local folders are the lowest trust tier: re-run with --trust-local after reviewing the code"
    return None


# ------------------------------------------------------------------ venv integrity
def _record_lines(venv: Path) -> list[tuple[Path, str]]:
    try:
        sp = inst.site_packages(venv)
    except inst.InstallError:
        return []
    return [
        (p, p.read_text(encoding="utf-8", errors="replace")) for p in sorted(sp.glob("*.dist-info/RECORD"))
    ]


def venv_hash(venv: Path) -> str:
    """Fingerprint of what pip installed: sorted `RECORD` files (they carry every file hash)."""
    h = hashlib.sha256()
    for path, text in _record_lines(venv):
        h.update(path.parent.name.encode() + b"\0" + text.encode() + b"\0")
    return h.hexdigest()


def record_problems(venv: Path, limit: int = 5) -> list[str]:
    """Re-hash every file a `RECORD` lists (inside site-packages) against its recorded sha256."""
    problems: list[str] = []
    checked = 0
    for record, text in _record_lines(venv):
        base = record.parent.parent
        for line in text.splitlines():
            parts = line.rsplit(",", 2)
            if len(parts) != 3 or not parts[1].startswith("sha256="):
                continue
            rel, digest = parts[0], parts[1][7:]
            target = (base / rel).resolve()
            if not target.is_relative_to(base.resolve()) or target.suffix == ".pyc":
                continue
            checked += 1
            if checked > MAX_RECORD_FILES:
                return problems
            try:
                got = base64.urlsafe_b64encode(hashlib.sha256(target.read_bytes()).digest()).rstrip(b"=")
            except OSError:
                problems.append(f"{rel}: missing")
            else:
                if got.decode() != digest:
                    problems.append(f"{rel}: hash differs from RECORD")
            if len(problems) >= limit:
                return problems
    return problems


def installed_problem(home: Path, name: str) -> str | None:
    """Doctor/host check of an installed plugin's venv against its lock (None = intact)."""
    lock = inst.read_lock(home, name)
    venv = inst.install_dir(home, name) / ".venv"
    if lock is None or not inst.venv_python(venv).exists():
        return None  # nothing installed yet: `install()` builds and pins it
    pinned = lock.get("venv_sha256")
    if pinned and pinned != venv_hash(venv):
        return "venv contents changed since install (venv_sha256 mismatch)"
    if bad := record_problems(venv):
        return "venv file tampered: " + "; ".join(bad)
    entry = lock.get("entry")
    if isinstance(entry, str) and entry.startswith("ep:"):
        names = {e["value"] for e in venv_entry_points(venv)}
        if entry[3:] not in names:
            return f"entry point {entry[3:]} is no longer installed in the venv"
    return None


# ------------------------------------------------------------------ provenance
def _is_bundled(src: Path) -> bool:
    from auoreo.core.paths import bundled_root

    return src.resolve().parent == (bundled_root() / "plugins").resolve()


def bundled_signature(settings: Settings, name: str, src: Path, version: str) -> dict[str, Any]:
    rows = read_signatures(src.parent)
    check = verify_entry(
        name, version, inst.folder_hash(src), rows.get(name), settings.i6.plugins.extra_pubkeys
    )
    return {"signed": check.ok, "signed_by": check.signed_by, "signature": check.reason}


def provenance(settings: Settings, name: str, src: Path, version: str) -> dict[str, Any]:
    """`{tier, registry, signed_by, …}` for a discovered plugin folder (see the module doc)."""
    if _is_bundled(src):
        sig = bundled_signature(settings, name, src, version)
        return {"tier": "bundled", "registry": "bundled", **sig}
    home_dir = (settings.home / "plugins").resolve()
    if src.resolve().parent == home_dir:
        from auoreo.plugins.quarantine import read_marker

        lock = inst.read_lock(settings.home, name) or {}
        marker = read_marker(settings.home, name) or {}
        keep: dict[str, Any] = {k: lock[k] for k in ("package", "entry") if k in lock}
        return {
            "tier": lock.get("tier") or marker.get("tier") or "local-path",
            "registry": lock.get("registry") or marker.get("registry") or "local",
            "signed_by": lock.get("signed_by"),
            **keep,
        }
    return {"tier": "local-path", "registry": "local", "signed_by": None}


def refusal(settings: Settings, prov: dict[str, Any]) -> str | None:
    """Why a plugin with this provenance must not start (None = it may)."""
    cfg = settings.i6.plugins
    tier = prov.get("tier")
    if tier == "url":
        return "URL-sourced plugins never run"
    if tier == "bundled" and not prov.get("signed") and cfg.bundled_signatures == "enforce":
        return f"bundled plugin signature check failed: {prov.get('signature', 'unsigned')}"
    pkg = prov.get("package") if isinstance(prov.get("package"), dict) else None
    if tier == "allow-listed" and pkg is not None:
        registry = str(prov.get("registry", ""))
        if registry == "allow-list":
            allowed = cfg.allow_list.get(str(pkg.get("name")))
            if allowed is None or (allowed.version, allowed.sha256) != (
                pkg.get("version"),
                pkg.get("sha256"),
            ):
                return f"{pkg.get('name')} is no longer allow-listed with the pinned version/sha256"
        elif registry not in cfg.taps:
            return f"tap {registry} is no longer configured (i6.plugins.taps)"
    return None


# ------------------------------------------------------------------ entry points
def _ep_row(ep: md.EntryPoint, group: str) -> dict[str, Any]:
    dist = getattr(ep, "dist", None)
    return {
        "name": ep.name,
        "value": ep.value,
        "group": group,
        "dist": dist.metadata["Name"] if dist is not None else None,
        "version": dist.version if dist is not None else None,
    }


def entry_point_plugins(groups: list[str]) -> list[dict[str, Any]]:
    """Plugins pip-installed into THIS environment (listed only; never imported or started)."""
    rows = [_ep_row(ep, g) for g in groups for ep in md.entry_points(group=g)]
    return [{**r, "status": "quarantined", "tier": "allow-listed"} for r in rows]


def venv_entry_points(
    venv: Path, groups: tuple[str, ...] = ("auoreo.plugin", "oreo.plugin")
) -> list[dict[str, Any]]:
    """Entry points declared by distributions inside a plugin venv (metadata only)."""
    try:
        sp = inst.site_packages(venv)
    except inst.InstallError:
        return []
    out = []
    for dist in md.distributions(path=[str(sp)]):
        for g in groups:
            for ep in dist.entry_points.select(group=g):
                out.append({**_ep_row(ep, g), "dist": dist.metadata["Name"], "version": dist.version})
    return out


# ------------------------------------------------------------------ host gates
async def pre_start(host: PluginHost, st: PluginState) -> dict[str, Any] | None:
    """Tier/signature gate before install+spawn; None = refused (status `unverified`)."""
    prov = provenance(host.s.settings, st.name, st.src, st.manifest.version)
    st.trust = prov
    if (why := refusal(host.s.settings, prov)) is None:
        return prov
    st.status, st.error = "unverified", why
    await host.s.emit("server", "plugin.unverified", {"name": st.name, "error": why})
    return None


async def post_install(host: PluginHost, st: PluginState) -> bool:
    """Venv integrity after install; a mismatch quarantines the plugin (spec §25.15 L1081)."""
    from auoreo.plugins.quarantine import quarantine

    if (why := installed_problem(host.home, st.name)) is None:
        return True
    if inst.install_dir(host.home, st.name).resolve() == st.src.resolve():
        quarantine(host.home, st.name, source=str(st.src), tier=str(st.trust.get("tier")), reason=why)
    st.status, st.error = "quarantined", why
    await host.s.audit.record(
        actor=f"plugin:{st.name}", action="plugin.quarantine", decision="deny", reason=why[:300]
    )
    await host.s.emit("server", "plugin.quarantined", {"name": st.name, "error": why})
    return False


def row_extra(st: PluginState) -> dict[str, Any]:
    return {"tier": st.trust.get("tier"), "signed_by": st.trust.get("signed_by")}


__all__ = [
    "TIER_RANK",
    "Tier",
    "admit",
    "classify",
    "entry_point_plugins",
    "installed_problem",
    "post_install",
    "pre_start",
    "provenance",
    "record_problems",
    "refusal",
    "row_extra",
    "venv_entry_points",
    "venv_hash",
]
