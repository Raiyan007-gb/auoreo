"""F7 spike: size + cold start of onefile `auoreo` binaries (docs/spikes/nuitka.md).

    python scripts/spikes/coldstart.py dist/pyinstaller/auoreo dist/nuitka/auoreo [--runs 5]

For each binary: size, then the wall time of `--version` and `doctor` over N runs (median,
min, max). Every run uses a fresh AUOREO_HOME, so `doctor` measures a first start. The onefile
unpack happens on every run (PyInstaller's default runtime_tmpdir; Nuitka's default
`{TEMP}/onefile_{PID}_{TIME}`), so these are cold starts as far as the binary is concerned.
The OS page cache is warm after the first run; run 1 is reported separately.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def timed(cmd: list[str], env: dict[str, str]) -> tuple[float, int, str]:
    t0 = time.perf_counter()
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    lines = (p.stdout + p.stderr).strip().splitlines()
    return time.perf_counter() - t0, p.returncode, lines[-1] if lines else ""


def measure(binary: Path, runs: int) -> dict[str, object]:
    out: dict[str, object] = {"binary": str(binary), "size_mb": round(binary.stat().st_size / 1e6, 1)}
    for args in (["--version"], ["doctor"]):
        times, codes, last = [], [], ""
        for _ in range(runs):
            home = tempfile.mkdtemp(prefix="auoreo-cold-")
            env = {**os.environ, "AUOREO_HOME": home, "AUOREO_GATEWAY": "fake"}
            secs, code, last = timed([str(binary), *args], env)
            times.append(secs)
            codes.append(code)
        out[" ".join(args)] = {
            "first_s": round(times[0], 2),
            "median_s": round(statistics.median(times), 2),
            "min_s": round(min(times), 2),
            "max_s": round(max(times), 2),
            "exit_codes": sorted(set(codes)),
            "last_line": last[:160],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("binaries", nargs="+", type=Path)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    print(json.dumps([measure(b, args.runs) for b in args.binaries], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
