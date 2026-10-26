"""`auoreo plugin pip-add <package>[==version]`: allow-listed, hash-pinned pip plugins (spec §15,
§25.15 L1076/L1077, §26.14).

Only `oreo-plugin-*` / `auoreo-plugin-*` distributions, and only when pinned by SHA-256 in
`i6.plugins.allow_list` (registry `allow-list`) or in a configured tap's index (registry = the
tap URL; a tap row's ed25519 `sig` must verify when present). The package goes into the
plugin's OWN venv, never the server's environment:

    <home>/plugins/<name>/{pyproject.toml, requirements.lock, plugin.yaml, lock.json, .venv/}

`requirements.lock` holds `<pkg>==<ver> --hash=sha256:<pin>` (or `<pkg> @ <url> --hash=…`)
plus the entry's pinned dependency lines, installed with `uv pip install --require-hashes`.
The `auoreo.plugin` (or `oreo.plugin`) entry point is then read from the venv's metadata
WITHOUT importing anything; `plugin.yaml` comes from the package (or is synthesized) with
`entry` set to that entry point. The plugin lands QUARANTINED (tier `allow-listed`): it runs
only after `auoreo plugin verify` + `auoreo plugin enable`.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from auoreo.harness.manifests import PluginManifest
from auoreo.plugins import install as inst
from auoreo.plugins.quarantine import quarantine
from auoreo.plugins.registry import PACKAGE_RE, index
from auoreo.plugins.signing import verify_entry
from auoreo.plugins.trust import venv_entry_points

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

SPEC_RE = re.compile(r"^(?P<pkg>[a-z0-9][a-z0-9-]*)(?:==(?P<ver>[A-Za-z0-9.+!_-]+))?$")


class TrustError(ValueError):
    """The package may not be installed (not allow-listed, unpinned, bad signature, …)."""


@dataclass(frozen=True)
class Resolved:
    package: str
    version: str
    sha256: str
    registry: str
    requirements: tuple[str, ...] = ()
    url: str | None = None
    signed_by: str | None = None

    @property
    def name(self) -> str:
        return self.package.split("-plugin-", 1)[1].replace("-", "_")

    def lock_lines(self) -> list[str]:
        head = f"{self.package} @ {self.url}" if self.url else f"{self.package}=={self.version}"
        return [f"{head} --hash=sha256:{self.sha256}", *self.requirements]


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


async def resolve(settings: Settings, spec: str) -> Resolved:
    m = SPEC_RE.fullmatch(spec.strip().lower())
    if m is None or not PACKAGE_RE.fullmatch(m["pkg"]):
        raise TrustError(f"'{spec}' is not an oreo-plugin-* / auoreo-plugin-* package spec")
    pkg, ver = m["pkg"], m["ver"]
    cfg = settings.i6.plugins
    if (allowed := cfg.allow_list.get(pkg)) is not None:
        if ver and ver != allowed.version:
            raise TrustError(f"{pkg} is allow-listed at {allowed.version}, not {ver}")
        return Resolved(
            pkg, allowed.version, allowed.sha256, "allow-list", tuple(allowed.requirements), allowed.url
        )
    entries, errors = await index(settings)
    entry = next((e for e in entries if e.package == pkg and (ver is None or e.version == ver)), None)
    if entry is None:
        hint = f" (tap errors: {'; '.join(errors)})" if errors else ""
        raise TrustError(
            f"{pkg} is not allow-listed: pin it in i6.plugins.allow_list.{pkg} = "
            f"{{version, sha256}} or add a tap that pins it{hint}"
        )
    signed_by = None
    if entry.signature:
        check = verify_entry(
            pkg,
            entry.version,
            entry.sha256,
            {**entry.signature, "version": entry.version, "sha256": entry.sha256},
            cfg.extra_pubkeys,
        )
        if not check.ok:
            raise TrustError(
                f"tap {entry.tap}: signature for {pkg} {entry.version} is invalid ({check.reason})"
            )
        signed_by = check.signed_by
    return Resolved(pkg, entry.version, entry.sha256, entry.tap, entry.requirements, entry.url, signed_by)


def _dist(venv: Path, package: str) -> md.Distribution | None:
    sp = inst.site_packages(venv)
    return next(
        (d for d in md.distributions(path=[str(sp)]) if _normalize(d.metadata["Name"]) == package), None
    )


def _manifest_doc(r: Resolved, dist: md.Distribution | None, entry: str) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for f in (dist.files or []) if dist is not None else []:
        if f.name == "plugin.yaml":
            loaded = yaml.safe_load(f.read_text(encoding="utf-8") or "")
            doc = loaded if isinstance(loaded, dict) else {}
            break
    summary = dist.metadata.get("Summary") if dist is not None else None
    doc.setdefault("description", summary or r.package)
    doc.setdefault("provides", ["tools"])
    return {**doc, "name": r.name, "version": r.version, "entry": entry, "enabled": True}


async def pip_add(settings: Settings, spec: str, *, force: bool = False) -> dict[str, Any]:
    """Resolve → pin → venv install → read entry point → plugin.yaml → quarantine."""
    r = await resolve(settings, spec)
    home = settings.home
    dest = inst.install_dir(home, r.name)
    if (dest / "plugin.yaml").exists() and not force:
        raise TrustError(f"{dest} exists; pass --force to replace it")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{r.package}-host"\nversion = "{r.version}"\n'
        f'requires-python = ">={settings.plugins_python}"\ndependencies = []\n',
        encoding="utf-8",
    )
    (dest / "requirements.lock").write_text("\n".join(r.lock_lines()) + "\n", encoding="utf-8")
    prov = {
        "tier": "allow-listed",
        "registry": r.registry,
        "signed_by": r.signed_by,
        "package": {"name": r.package, "version": r.version, "sha256": r.sha256},
    }
    try:
        installed = await inst.install(
            r.name,
            dest,
            home,
            python=settings.plugins_python,
            seed=settings.plugins_seed,
            timeout=settings.i6.plugins.pip_timeout_s,
            provenance=prov,
        )
        venv = installed.dir / ".venv"
        eps = [
            e
            for e in venv_entry_points(venv, tuple(settings.i6.plugins.entry_point_groups))
            if _normalize(str(e["dist"])) == r.package
        ]
        if not eps:
            raise TrustError(f"{r.package} declares no auoreo.plugin / oreo.plugin entry point")
        doc = _manifest_doc(r, _dist(venv, r.package), eps[0]["value"])
        PluginManifest.model_validate(doc)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    (dest / "plugin.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    lock = {**installed.lock, "sha256": inst.folder_hash(dest), "entry": f"ep:{eps[0]['value']}"}
    (dest / "lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    sha = quarantine(home, r.name, source=f"{r.registry}:{r.package}=={r.version}", tier="allow-listed")
    return {
        "name": r.name,
        "dir": str(dest),
        "package": r.package,
        "version": r.version,
        "registry": r.registry,
        "signed_by": r.signed_by,
        "entry": eps[0]["value"],
        "sha256": sha,
    }


__all__ = ["Resolved", "TrustError", "pip_add", "resolve"]
