"""Plugin quarantine (spec §25.15): `plugin add → quarantine → verify (doctor) → enable`.

`auoreo plugin add` copies a folder into `<home>/plugins/<name>/` and drops a
`quarantine.json` marker next to it, recording the folder hash it copied. The plugin host
never starts a quarantined plugin. `plugin verify` re-checks the copy (manifest loads, hash
still matches what was added, config schema validates, declared secrets are reported) and stamps
the marker `verified`; `plugin enable` removes the marker only when the verified hash is
still the folder's hash. Every step writes an audit row (`plugin.add|verify|enable`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auoreo.plugins.install import folder_hash, install_dir

MARKER = "quarantine.json"


@dataclass
class Report:
    name: str
    ok: bool
    sha256: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """Non-blocking findings (a declared secret not set yet: the plugin reports it at start)."""


def marker_path(home: Path, name: str) -> Path:
    return install_dir(home, name) / MARKER


def read_marker(home: Path, name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(marker_path(home, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {}


def is_quarantined(home: Path, name: str) -> bool:
    return marker_path(home, name).exists()


def _write(home: Path, name: str, data: dict[str, Any]) -> None:
    path = marker_path(home, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp.replace(path)


def quarantine(home: Path, name: str, *, source: str, tier: str, reason: str = "new plugin") -> str:
    """Mark `<home>/plugins/<name>` quarantined; returns the recorded folder hash."""
    sha = folder_hash(install_dir(home, name))
    _write(
        home,
        name,
        {
            "reason": reason,
            "source": source,
            "tier": tier,
            "sha256": sha,
            "added_at": datetime.now(UTC).isoformat(),
            "verified": None,
        },
    )
    return sha


def verify(home: Path, name: str, settings: Any) -> Report:
    """Doctor-style checks on the quarantined copy; stamps `verified` when all pass."""
    from auoreo.harness.discovery import Candidate, Root
    from auoreo.harness.loader import LoadError, load_plugin
    from auoreo.plugins.connectors import file_config
    from auoreo.plugins.host import validate_config

    folder = install_dir(home, name)
    sha = folder_hash(folder) if folder.is_dir() else ""
    report = Report(name, False, sha)
    marker = read_marker(home, name)
    if not folder.is_dir():
        report.problems.append(f"{folder} does not exist")
        return report
    try:
        loaded = load_plugin(
            Candidate("plugins", name, folder / "plugin.yaml", Root("runtime", folder.parent))
        )
    except (LoadError, OSError) as exc:
        report.problems.append(f"manifest: {exc}")
        return report
    m = loaded.manifest
    if marker is not None and marker.get("sha256") and marker["sha256"] != sha:
        report.problems.append("folder changed since `plugin add` (hash mismatch); re-add it")
    if m.sha256 and m.sha256 != sha:
        report.problems.append("plugin.yaml sha256 pin does not match the folder")
    from auoreo.plugins.trust import installed_problem  # --- I6: venv re-hash vs lock

    if (venv_problem := installed_problem(home, name)) is not None:
        report.problems.append(venv_problem)
    try:
        validate_config(m, {**getattr(settings, "plugins", {}).get(name, {}), **file_config(home, name)})
    except Exception as exc:
        report.problems.append(f"config: {exc}")
    report.warnings += _missing_secrets(m.consumes, settings)
    report.ok = not report.problems
    if report.ok and marker is not None:
        _write(home, name, {**marker, "verified": sha, "verified_at": datetime.now(UTC).isoformat()})
    return report


def _missing_secrets(consumes: list[str], settings: Any) -> list[str]:
    from auoreo.core.secrets import SecretStore

    names = [c.split(":", 1)[1] for c in consumes if c.startswith("secrets:")]
    if not names:
        return []
    store = SecretStore(settings.home, settings.keyring)
    return [f"secret '{n}' is not set (auoreo secrets set {n})" for n in names if not store.get(n)]


def enable(home: Path, name: str) -> str | None:
    """Release the quarantine. Returns an error string when it must stay quarantined."""
    marker = read_marker(home, name)
    if marker is None:
        return None if install_dir(home, name).is_dir() else f"no user-installed plugin '{name}'"
    verified = marker.get("verified")
    if not verified:
        return f"'{name}' has not passed `auoreo plugin verify {name}`"
    if verified != folder_hash(install_dir(home, name)):
        return f"'{name}' changed after it was verified; run `auoreo plugin verify {name}` again"
    marker_path(home, name).unlink(missing_ok=True)
    return None


def quarantined_names(home: Path) -> list[str]:
    root = home / "plugins"
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.glob(f"*/{MARKER}"))
