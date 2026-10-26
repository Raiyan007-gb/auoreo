#!/usr/bin/env python3
"""Per-file coverage floor over a coverage.py JSON report (spec §20.4).

`--cov-fail-under` only checks the global total; this fails when any measured file under
`src/auoreo/` is below `--per-file` percent (files matching `--exempt` globs are skipped —
optional-extra / device code that CI cannot exercise). Uncovered files are delete
candidates: raise the floor as coverage grows, never lower it to pass.

    uv run python scripts/check_coverage.py .artifacts/coverage.json --per-file 20
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

DEFAULT_EXEMPT = ("*/__main__.py",)


def below_floor(report: dict[str, object], floor: float, exempt: tuple[str, ...]) -> list[tuple[str, float]]:
    files = report.get("files")
    assert isinstance(files, dict), "not a coverage.py JSON report"
    out = []
    for name, data in files.items():
        norm = name.replace("\\", "/")
        if any(fnmatch.fnmatch(norm, pat) for pat in exempt):
            continue
        pct = float(data["summary"]["percent_covered"])
        if pct < floor:
            out.append((norm, round(pct, 1)))
    return sorted(out, key=lambda kv: kv[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--per-file", type=float, required=True, help="minimum percent for every file")
    ap.add_argument("--exempt", action="append", default=[], help="glob to skip (repeatable)")
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    offenders = below_floor(report, args.per_file, (*DEFAULT_EXEMPT, *args.exempt))
    total = float(report["totals"]["percent_covered"])
    print(f"coverage: {total:.1f}% total, {len(report['files'])} files, per-file floor {args.per_file}%")
    for name, pct in offenders:
        print(f"  BELOW FLOOR {pct:5.1f}%  {name}")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
