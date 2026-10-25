"""Inputs for `auoreo.spec` (kept importable so tests can check them without PyInstaller).

Tool handlers, backend impls and plugin entry points are loaded by file path at runtime, so
PyInstaller's import graph never sees their third-party imports. `definition_imports()` reads
them with `ast` and returns the installed top-level modules to add as hidden imports.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("tools", "backends", "plugins", "hooks")

EXCLUDED_MODULES: tuple[str, ...] = (
    # optional heavy stacks: install from a wheel (`uv tool install 'auoreo[...]'`) to use them
    "torch",
    "sentence_transformers",
    "huggingface_hub",
    "transformers",
    "playwright",
    "tensorflow",
    "onnxruntime",
    "cv2",
    "mediapipe",
    "insightface",
    "rfdetr",
    "ultralytics",
    "faster_whisper",
    "kittentts",
    "sherpa_onnx",
    "e2b",
    "modal",
    "daytona",
    # dev / test only
    "pytest",
    "mypy",
    "ruff",
    "IPython",
    "tkinter",
)


def bundled_dirs(root: Path = ROOT) -> tuple[str, ...]:
    """`auoreo.core.paths.BUNDLED_DIRS` without importing the package."""
    spec = importlib.util.spec_from_file_location("_auoreo_paths", root / "src/auoreo/core/paths.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.BUNDLED_DIRS)


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def definition_imports(root: Path = ROOT) -> list[str]:
    """Installed, non-stdlib, non-excluded top-level modules imported by definition code."""
    found: set[str] = set()
    for kind in SCANNED:
        for py in (root / kind).rglob("*.py"):
            found |= _imports(py)
    keep = []
    for name in sorted(found - set(sys.stdlib_module_names) - set(EXCLUDED_MODULES) - {"auoreo"}):
        try:
            if importlib.util.find_spec(name) is not None:
                keep.append(name)
        except (ImportError, ValueError):
            continue
    return keep


def data_dirs(root: Path = ROOT) -> list[tuple[str, str]]:
    """(source, dest) for the definition folders + web/dist, at the bundle root."""
    pairs = [(str(root / d), d) for d in bundled_dirs(root) if (root / d).is_dir()]
    dist = root / "web" / "dist"
    if (dist / "index.html").is_file():
        pairs.append((str(dist), "web/dist"))
    return pairs


if __name__ == "__main__":
    print("hidden imports:", definition_imports())
    print("data:", [d for _, d in data_dirs()])
