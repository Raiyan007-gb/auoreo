"""I6 doctor rows: plugin trust (spec §25.15 L1081, §26.14) and the GitHub `/meta` cache (§19.11).

- every installed plugin (`<home>/plugins/<name>/lock.json`): the folder is re-hashed against
  the lock's `sha256`, the venv against `venv_sha256` (+ every `RECORD` file hash), and a
  pip-installed plugin's entry point must still be present. A mismatch is an error: the host
  quarantines such a plugin at its next start (doctor itself never repairs anything).
- every bundled plugin: its ed25519 signature (`plugins/SIGNATURES.json`) against the pinned keys.
- `importlib.metadata` entry points (`auoreo.plugin` / `oreo.plugin`) in the server's own
  environment are listed as quarantined: they never run from there.
- webhook routes using `allowed_cidrs: [github]`: warn when the cached ranges are > 7 days old.
"""

from __future__ import annotations

from pathlib import Path

from auoreo.core import paths
from auoreo.core.diagnostics import Check, Level
from auoreo.core.settings import Settings


def _installed_rows(st: Settings) -> list[Check]:
    from auoreo.plugins import install as inst
    from auoreo.plugins.trust import installed_problem

    out: list[Check] = []
    root = st.home / "plugins"
    for lock_path in sorted(root.glob("*/lock.json")) if root.is_dir() else []:
        name = lock_path.parent.name
        lock = inst.read_lock(st.home, name) or {}
        src = lock_path.parent if (lock_path.parent / "plugin.yaml").is_file() else None
        problems = []
        if src is not None and lock.get("sha256") != inst.folder_hash(src):
            problems.append("folder differs from lock.json sha256")
        if (why := installed_problem(st.home, name)) is not None:
            problems.append(why)
        tier = lock.get("tier", "?")
        if problems:
            remedy = f"auoreo plugin verify {name} (re-add or reinstall it)"
            out.append(Check(f"plugin-trust:{name}", "error", f"{tier}: " + "; ".join(problems), remedy))
        else:
            signed = f", signed by {lock['signed_by']}" if lock.get("signed_by") else ""
            out.append(
                Check(f"plugin-trust:{name}", "ok", f"{tier} {lock.get('version', '')}{signed}: intact")
            )
    return out


def _bundled_rows(st: Settings, plugins_dir: Path) -> list[Check]:
    import yaml

    from auoreo.plugins.trust import bundled_signature

    mode = st.i6.plugins.bundled_signatures
    if mode == "off" or not plugins_dir.is_dir():
        return []
    bad: list[str] = []
    good = 0
    for folder in sorted(p for p in plugins_dir.iterdir() if (p / "plugin.yaml").is_file()):
        meta = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8")) or {}
        sig = bundled_signature(st, str(meta.get("name", folder.name)), folder, str(meta.get("version", "")))
        if sig["signed"]:
            good += 1
        else:
            bad.append(f"{folder.name} ({sig['signature']})")
    if not bad:
        return [Check("plugin-signatures", "ok", f"{good} bundled plugin(s) signed by a pinned key")]
    level: Level = "error" if mode == "enforce" else "warn"
    remedy = "python packaging/sign_plugins.py sign (release owner) or restore the shipped files"
    return [Check("plugin-signatures", level, "unsigned/stale: " + ", ".join(bad), remedy)]


def _entry_point_rows(st: Settings) -> list[Check]:
    from auoreo.plugins.trust import entry_point_plugins

    rows = entry_point_plugins(st.i6.plugins.entry_point_groups)
    if not rows:
        return []
    names = ", ".join(f"{r['dist'] or r['name']} ({r['value']})" for r in rows[:10])
    return [
        Check(
            "plugin-entry-points",
            "warn",
            f"{len(rows)} entry point(s) in the server env, listed only (quarantined): {names}",
            "install it into its own venv: auoreo plugin pip-add <package>",
        )
    ]


def _github_meta_rows(st: Settings) -> list[Check]:
    from auoreo.automation.github_meta import doctor_row
    from auoreo.harness.discovery import default_roots
    from auoreo.harness.registry import Harness

    if not st.webhooks_enabled:
        return []
    harness = Harness(default_roots(st.home, Path.cwd(), paths.bundled_root(), st.extra_roots))
    routes = sorted(n for n, d in harness.webhooks.items() if "github" in d.manifest.allowed_cidrs)
    row = doctor_row(st, routes)
    return [Check(row[0], row[1], row[2], row[3])] if row else []  # type: ignore[arg-type]


async def i6_checks(st: Settings) -> list[Check]:
    return [
        *_installed_rows(st),
        *_bundled_rows(st, paths.bundled_root() / "plugins"),
        *_entry_point_rows(st),
        *_github_meta_rows(st),
    ]


__all__ = ["i6_checks"]
