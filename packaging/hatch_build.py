"""Hatch build hook (W3-A): ship the bundled definitions and the built SPA inside the package.

Wheel layout: `auoreo/_bundled/{agents,tools,...,memory}/**` + `auoreo/_bundled/web/dist/**`,
which `auoreo.core.paths.bundled_root()` finds for a non-checkout install. The sdist carries
the same folders at the repo-relative paths (plus `web/dist`) so a wheel built from it is
complete too.

Spec: never ship a wheel without `web/dist`. A wheel build fails unless `web/dist/index.html`
exists; `AUOREO_ALLOW_NO_WEB_DIST=1` is the escape hatch for Python-only CI jobs (the server
then runs API-only, exactly like a checkout without a web build).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SKIP_PARTS = {"__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
ESCAPE_ENV = "AUOREO_ALLOW_NO_WEB_DIST"


def bundled_dirs(root: Path) -> tuple[str, ...]:
    """`auoreo.core.paths.BUNDLED_DIRS`, loaded without importing the package."""
    spec = importlib.util.spec_from_file_location("_auoreo_paths", root / "src/auoreo/core/paths.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.BUNDLED_DIRS)


def iter_files(base: Path) -> list[Path]:
    return sorted(
        p
        for p in base.rglob("*")
        if p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.suffix not in SKIP_SUFFIXES
    )


class BundledDefinitionsHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name == "wheel" and version == "editable":
            return  # editable installs read the checkout directly
        root = Path(self.root)
        dist = root / "web" / "dist"
        if not (dist / "index.html").is_file():
            if os.environ.get(ESCAPE_ENV) != "1":
                raise RuntimeError(
                    "web/dist/index.html is missing: run `cd web && npm ci && npm run build` before "
                    f"building (or set {ESCAPE_ENV}=1 for an API-only build). Spec: never ship a "
                    "wheel without web/dist."
                )
            self.app.display_warning(f"{ESCAPE_ENV}=1: building without web/dist (API-only)")
        prefix = "auoreo/_bundled/" if self.target_name == "wheel" else ""
        mapping: dict[str, str] = build_data.setdefault("force_include", {})
        for name in (*bundled_dirs(root), "web/dist"):
            base = root / name
            if not base.is_dir():
                continue
            for f in iter_files(base):
                mapping[str(f)] = prefix + f.relative_to(root).as_posix()
