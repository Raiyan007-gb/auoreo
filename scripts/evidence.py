#!/usr/bin/env python3
"""Collect gate outputs into `.artifacts/evidence.json` (spec §20.4 "evidence" tier).

Runs each gate as a subprocess from the repo root and records exit code, duration and the
output tail; pytest is run with `--junitxml` (the default `addopts` hide the summary) and,
with `--coverage`, `--cov-report=json` so test counts and coverage come from files rather
than scraped text. The manifest also carries the git sha/branch/dirty flag, tool versions
and a UTC timestamp. Exit status is 1 when any gate failed (the manifest is still written).

    uv run python scripts/evidence.py                    # all gates, no coverage
    uv run python scripts/evidence.py --coverage         # + coverage totals
    uv run python scripts/evidence.py --only openapi,harness_validate
    uv run python scripts/evidence.py --pytest-args "tests/unit -x"
    uv run python scripts/evidence.py --skip pytest     # after scripts/coverage.sh (CI)

`.artifacts/` is gitignored; CI uploads it as a build artifact.
"""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".artifacts"
TAIL = 40


@dataclass
class Gate:
    name: str
    cmd: list[str]
    parse: str | None = None  # key into PARSERS, run on success or failure


@dataclass
class GateResult:
    name: str
    cmd: str
    ok: bool
    returncode: int
    seconds: float
    tail: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def parse_junit(path: Path) -> dict[str, Any]:
    """Totals from a JUnit XML file (`<testsuites>` or a bare `<testsuite>`)."""
    if not path.is_file():
        return {"error": f"missing {path.name}"}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    keys = ("tests", "failures", "errors", "skipped")
    totals = {k: sum(int(s.get(k, 0) or 0) for s in suites) for k in keys}
    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    totals["seconds"] = round(sum(float(s.get("time", 0) or 0) for s in suites), 2)
    return totals


def parse_coverage(path: Path, worst: int = 15) -> dict[str, Any]:
    """Global percent + the `worst` files from a coverage.py JSON report."""
    if not path.is_file():
        return {"error": f"missing {path.name}"}
    data = json.loads(path.read_text(encoding="utf-8"))
    files = {name: f["summary"]["percent_covered"] for name, f in data.get("files", {}).items()}
    lowest = sorted(files.items(), key=lambda kv: kv[1])[:worst]
    return {
        "percent": round(float(data["totals"]["percent_covered"]), 2),
        "statements": data["totals"]["num_statements"],
        "missing": data["totals"]["missing_lines"],
        "files": len(files),
        "lowest": [{"file": n, "percent": round(p, 1)} for n, p in lowest],
    }


PARSERS = {
    "junit": lambda: parse_junit(ARTIFACTS / "junit.xml"),
    "junit+coverage": lambda: {
        **parse_junit(ARTIFACTS / "junit.xml"),
        "coverage": parse_coverage(ARTIFACTS / "coverage.json"),
    },
}


def gates(pytest_args: list[str], coverage: bool) -> list[Gate]:
    uv = ["uv", "run"]
    test_cmd = [*uv, "pytest", "-p", "no:warnings", f"--junitxml={ARTIFACTS / 'junit.xml'}", *pytest_args]
    if coverage:
        test_cmd += ["--cov", f"--cov-report=json:{ARTIFACTS / 'coverage.json'}", "--cov-report="]
    return [
        Gate("ruff_check", [*uv, "ruff", "check", "."]),
        Gate("ruff_format", [*uv, "ruff", "format", "--check", "."]),
        Gate("mypy", [*uv, "mypy", "src"]),
        Gate("openapi", [*uv, "python", "scripts/check_openapi.py", "--check"]),
        Gate("harness_validate", [*uv, "auoreo", "harness", "validate"]),
        Gate("pytest", test_cmd, "junit+coverage" if coverage else "junit"),
    ]


def run_gate(gate: Gate, cwd: Path = ROOT) -> GateResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(gate.cmd, cwd=cwd, capture_output=True, text=True, check=False)
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError as exc:
        code, out = 127, str(exc)
    result = GateResult(
        name=gate.name,
        cmd=shlex.join(gate.cmd),
        ok=code == 0,
        returncode=code,
        seconds=round(time.monotonic() - start, 2),
        tail=out.strip().splitlines()[-TAIL:],
    )
    if gate.parse:
        try:
            result.details = PARSERS[gate.parse]()
        except Exception as exc:  # a broken report must not hide the gate result
            result.details = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
        return out.stdout.strip()
    except FileNotFoundError:
        return ""


def environment() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": {
            "sha": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def collect(selected: list[Gate], out: Path) -> dict[str, Any]:
    """Run `selected` in order and write the manifest to `out`; returns it."""
    results = []
    for gate in selected:
        print(f"[evidence] {gate.name}: {shlex.join(gate.cmd)}", flush=True)
        res = run_gate(gate)
        print(f"[evidence] {gate.name}: {'ok' if res.ok else 'FAILED'} ({res.seconds}s)", flush=True)
        results.append(res)
    manifest = {
        **environment(),
        "ok": all(r.ok for r in results),
        "gates": {r.name: asdict(r) for r in results},
        # whatever reports are on disk (from this run's pytest gate or an earlier
        # `scripts/coverage.sh`), so `--skip pytest` still records the counts
        "reports": {
            "junit": parse_junit(ARTIFACTS / "junit.xml"),
            "coverage": parse_coverage(ARTIFACTS / "coverage.json"),
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ARTIFACTS / "evidence.json")
    ap.add_argument("--only", default="", help="comma-separated gate names (default: all)")
    ap.add_argument("--skip", default="", help="comma-separated gate names to skip")
    ap.add_argument("--coverage", action="store_true", help="run pytest under coverage")
    ap.add_argument("--pytest-args", default="", help="extra pytest arguments (one string)")
    args = ap.parse_args(argv)
    all_gates = gates(shlex.split(args.pytest_args), args.coverage)
    only = {n for n in args.only.split(",") if n}
    skip = {n for n in args.skip.split(",") if n}
    unknown = (only | skip) - {g.name for g in all_gates}
    if unknown:
        ap.error(f"unknown gate(s): {', '.join(sorted(unknown))}")
    selected = [g for g in all_gates if (not only or g.name in only) and g.name not in skip]
    manifest = collect(selected, args.out)
    print(f"[evidence] wrote {args.out} (ok={manifest['ok']})")
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
