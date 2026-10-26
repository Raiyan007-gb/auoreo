"""G5c: `scripts/evidence.py` (gate manifest) and `scripts/check_coverage.py` (per-file floor)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_g5c_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def evidence() -> ModuleType:
    return _load("evidence")


@pytest.fixture(scope="module")
def check_coverage() -> ModuleType:
    return _load("check_coverage")


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="2" skipped="3" tests="20" time="4.5"/></testsuites>
"""


def _coverage_report(files: dict[str, float]) -> dict[str, object]:
    return {
        "totals": {"percent_covered": 71.25, "num_statements": 400, "missing_lines": 115},
        "files": {n: {"summary": {"percent_covered": p}} for n, p in files.items()},
    }


def test_parse_junit_and_coverage(evidence: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text(JUNIT)
    totals = evidence.parse_junit(tmp_path / "junit.xml")
    assert totals == {"tests": 20, "failures": 2, "errors": 1, "skipped": 3, "passed": 14, "seconds": 4.5}
    assert evidence.parse_junit(tmp_path / "nope.xml") == {"error": "missing nope.xml"}
    (tmp_path / "cov.json").write_text(json.dumps(_coverage_report({"a.py": 90.0, "b.py": 10.0})))
    cov = evidence.parse_coverage(tmp_path / "cov.json", worst=1)
    assert cov["percent"] == 71.25 and cov["files"] == 2
    assert cov["lowest"] == [{"file": "b.py", "percent": 10.0}]


def test_collect_writes_manifest_and_reports_failures(
    evidence: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evidence, "ARTIFACTS", tmp_path)
    (tmp_path / "junit.xml").write_text(JUNIT)
    py = sys.executable
    gates = [
        evidence.Gate("good", [py, "-c", "print('fine')"]),
        evidence.Gate("bad", [py, "-c", "import sys; print('broken'); sys.exit(3)"]),
        evidence.Gate("missing", ["definitely-not-a-binary-g5c"]),
    ]
    out = tmp_path / "evidence.json"
    manifest = evidence.collect(gates, out)
    assert json.loads(out.read_text()) == manifest
    assert manifest["ok"] is False and manifest["git"]["sha"]
    good, bad, missing = (manifest["gates"][n] for n in ("good", "bad", "missing"))
    assert good["ok"] and good["tail"] == ["fine"] and good["returncode"] == 0
    assert not bad["ok"] and bad["returncode"] == 3 and bad["tail"] == ["broken"]
    assert missing["returncode"] == 127
    assert manifest["reports"]["junit"]["tests"] == 20
    assert manifest["reports"]["coverage"] == {"error": "missing coverage.json"}


def test_gate_catalog_and_cli_selection(evidence: ModuleType) -> None:
    names = [g.name for g in evidence.gates([], coverage=True)]
    assert names == ["ruff_check", "ruff_format", "mypy", "openapi", "harness_validate", "pytest"]
    pytest_gate = evidence.gates(["tests/unit"], coverage=True)[-1]
    assert any(a.startswith("--junitxml=") for a in pytest_gate.cmd) and "--cov" in pytest_gate.cmd
    assert "tests/unit" in pytest_gate.cmd
    with pytest.raises(SystemExit):
        evidence.main(["--only", "nonexistent"])


def test_per_file_floor(
    check_coverage: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _coverage_report({"src/auoreo/a.py": 95.0, "src/auoreo/b.py": 12.0, "src/auoreo/__main__.py": 0})
    offenders = check_coverage.below_floor(report, 20, check_coverage.DEFAULT_EXEMPT)
    assert offenders == [("src/auoreo/b.py", 12.0)]
    path = tmp_path / "cov.json"
    path.write_text(json.dumps(report))
    assert check_coverage.main([str(path), "--per-file", "20"]) == 1
    assert "BELOW FLOOR  12.0%  src/auoreo/b.py" in capsys.readouterr().out
    assert check_coverage.main([str(path), "--per-file", "20", "--exempt", "*/b.py"]) == 0
