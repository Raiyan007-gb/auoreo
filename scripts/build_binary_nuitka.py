"""EXPERIMENTAL: build the onefile `auoreo` binary with Nuitka (F7 spike, docs/spikes/nuitka.md).

    uv run --with 'nuitka>=2.8' --with ordered-set --with zstandard --with patchelf \
        python scripts/build_binary_nuitka.py [--standalone] [--jobs N] [--dry-run]

PyInstaller (`scripts/build_binary.py`) stays the release builder. This script mirrors the
inclusions of `packaging/pyinstaller/auoreo.spec` (same `collect.py` inputs) so the two are
comparable:

- definition folders + `web/dist` -> `auoreo/_bundled/` inside the bundle. A Nuitka binary has
  no `sys._MEIPASS`; `auoreo.core.paths.bundled_root()` already falls back to
  `<package dir>/_bundled`, so no engine change is needed. `--include-data-dir` skips code
  files, and handlers are `.py` files loaded by path at runtime, so they are added again with
  the recursive `--include-data-files=src=dst/=**/*.py` form.
- Alembic scripts and the plugin runtime (`auoreo_plugin_rt.py`) as `.py` data files.
- `sqlite_vec/vec0.so` next to its package; litellm/tiktoken package data.
- every `auoreo`/`uvicorn`/`litellm` submodule (`--include-package`), minus `litellm.proxy`
  and the heavy optional stacks in `collect.EXCLUDED_MODULES`.

Output: `dist/nuitka/auoreo`; prints size, sha256 and the wall time of the build.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging" / "pyinstaller"))
from collect import EXCLUDED_MODULES, bundled_dirs, definition_imports  # noqa: E402

ENTRY = ROOT / "packaging" / "pyinstaller" / "entry.py"
BUNDLE = "auoreo/_bundled"
PACKAGE_CONFIG = ROOT / "scripts" / "spikes" / "auoreo.nuitka-package.config.yml"
"""Source workarounds for Nuitka optimizer crashes in third-party code (see the file)."""


def data_args(root: Path = ROOT) -> list[str]:
    """`--include-data-files` / `--include-package-data` flags (the spec's `datas`)."""
    out: list[str] = []
    for d in bundled_dirs(root):
        if (root / d).is_dir():  # non-code files, then the handlers/impls Nuitka calls "code"
            out.append(f"--include-data-dir={root / d}={BUNDLE}/{d}")
            if any((root / d).rglob("*.py")):
                out.append(f"--include-data-files={root / d}={BUNDLE}/{d}/=**/*.py")
    dist = root / "web" / "dist"
    if (dist / "index.html").is_file():
        out.append(f"--include-data-dir={dist}={BUNDLE}/web/dist")
    src = root / "src" / "auoreo"
    out += [
        f"--include-data-files={src / 'core' / 'alembic'}=auoreo/core/alembic/=**/*.py",
        f"--include-data-files={src / 'core' / 'alembic'}=auoreo/core/alembic/=**/*.mako",
        f"--include-data-files={src / 'plugins' / 'rt'}=auoreo/plugins/rt/=**/*.py",
        "--include-package-data=auoreo",
        "--include-package-data=litellm",
        "--include-package-data=tiktoken_ext",
    ]
    spec = importlib.util.find_spec("sqlite_vec")
    if spec is not None and spec.origin:
        for lib in Path(spec.origin).parent.glob("vec0.*"):
            if lib.suffix in {".so", ".dylib", ".dll"}:
                out.append(f"--include-data-files={lib}=sqlite_vec/{lib.name}")
    return out


def module_args(root: Path = ROOT) -> list[str]:
    """The spec's `hiddenimports` / `excludes`."""
    out = ["--include-package=auoreo", "--include-package=uvicorn", "--include-package=litellm"]
    out += ["--include-module=tiktoken_ext.openai_public", "--include-module=multiprocessing"]
    out += [f"--include-module={m}" for m in definition_imports(root)]
    nofollow = [*EXCLUDED_MODULES, "litellm.proxy", "litellm.tests"]
    out += [f"--nofollow-import-to={m}" for m in nofollow]
    return out


def command(args: argparse.Namespace) -> list[str]:
    mode = "--mode=standalone" if args.standalone else "--mode=onefile"
    return [
        sys.executable,
        "-m",
        "nuitka",
        mode,
        "--assume-yes-for-downloads",
        f"--jobs={args.jobs}",
        "--lto=no",
        f"--output-dir={args.dist}",
        "--output-filename=auoreo",
        f"--user-package-configuration-file={PACKAGE_CONFIG}",
        *module_args(),
        *data_args(),
        str(ENTRY),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--standalone", action="store_true", help="folder build (debug data files first)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--dist", type=Path, default=ROOT / "dist" / "nuitka")
    ap.add_argument("--dry-run", action="store_true", help="print the nuitka command and exit")
    args = ap.parse_args(argv)
    has_web = (ROOT / "web" / "dist" / "index.html").is_file()
    if not has_web and os.environ.get("AUOREO_ALLOW_NO_WEB_DIST") != "1":
        print("web/dist/index.html is missing: run `cd web && npm ci && npm run build`", file=sys.stderr)
        return 2
    cmd = command(args)
    print("+", " ".join(cmd), flush=True)
    if args.dry_run:
        return 0
    started = time.monotonic()
    subprocess.run(cmd, cwd=ROOT, check=True)
    elapsed = time.monotonic() - started
    out = next((p for p in sorted(args.dist.glob("auoreo*")) if p.is_file()), None)
    if out is None:
        print(f"build finished in {elapsed:.0f}s but no binary in {args.dist}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"built {out} ({out.stat().st_size / 1e6:.1f} MB) in {elapsed:.0f}s sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
