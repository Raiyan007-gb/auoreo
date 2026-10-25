"""Build the onefile `auoreo` binary with PyInstaller (W3-A).

    uv run --with 'pyinstaller>=6.22,<7' python scripts/build_binary.py [--platform-name]

Fails unless `web/dist/index.html` exists (spec: never ship without web/dist; set
AUOREO_ALLOW_NO_WEB_DIST=1 for an API-only build). Output: `dist/auoreo[.exe]`, or with
`--platform-name` `dist/auoreo-<platform>[.exe]` (the release asset name, e.g. auoreo-linux-x64).
Prints size and sha256. Signing happens afterwards in CI (docs/release.md).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "pyinstaller" / "auoreo.spec"


def platform_name() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from auoreo.core.upgrade import platform_key

    return platform_key()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform-name", action="store_true", help="name it auoreo-<platform>")
    ap.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = ap.parse_args(argv)
    has_web = (ROOT / "web" / "dist" / "index.html").is_file()
    if not has_web and os.environ.get("AUOREO_ALLOW_NO_WEB_DIST") != "1":
        print("web/dist/index.html is missing: run `cd web && npm ci && npm run build`", file=sys.stderr)
        return 2
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--clean",
        "--distpath",
        str(args.dist),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
    ]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)
    exe = ".exe" if sys.platform == "win32" else ""
    out = args.dist / f"auoreo{exe}"
    if args.platform_name:
        named = args.dist / f"auoreo-{platform_name()}{exe}"
        out.replace(named)
        out = named
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"built {out} ({out.stat().st_size / 1e6:.1f} MB) sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
