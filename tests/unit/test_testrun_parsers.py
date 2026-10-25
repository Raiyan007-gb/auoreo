"""F2 pure parsers in `auoreo.tools.testrun` against captured runner output."""

from __future__ import annotations

from pathlib import Path

from auoreo.tools import testrun

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "testrun"

JEST = """
FAIL src/sum.test.js
  ● math › adds numbers

    expect(received).toBe(expected) // Object.is equality

    Expected: 4
    Received: 5

      3 | test('adds numbers', () => {
    > 4 |   expect(sum(2, 2)).toBe(4);
        |                     ^

      at Object.toBe (src/sum.test.js:4:21)

PASS src/other.test.js

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 1 skipped, 6 passed, 8 total
Snapshots:   0 total
Time:        1.2 s
"""

VITEST = """
 ❯ src/a.test.ts (3 tests | 1 failed) 5ms
 Test Files  1 failed (1)
      Tests  1 failed | 2 passed (3)
"""

CARGO = """
running 3 tests
test tests::ok ... ok
test tests::ignored ... ignored
test tests::bad ... FAILED

failures:

---- tests::bad stdout ----

thread 'tests::bad' panicked at src/lib.rs:12:9:
assertion `left == right` failed
  left: 2
 right: 3

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out
"""

ESLINT = """[{"filePath": "/w/src/a.js", "messages": [
  {"ruleId": "no-unused-vars", "severity": 2, "message": "'x' is defined but never used.",
   "line": 3, "column": 7},
  {"ruleId": null, "severity": 1, "message": "Parsing warning", "line": 9, "column": 1}]},
 {"filePath": "/w/src/b.js", "messages": []}]"""

TSC = """src/app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/util.ts(2,1): error TS2304: Cannot find name 'foo'.
"""


def test_pytest_quiet_output() -> None:
    s = testrun.parse_pytest((FIX / "pytest_q.txt").read_text())
    assert (s.passed, s.failed, s.skipped, s.errors) == (1, 2, 1, 0)
    first = s.failures[0]
    assert first.name == "tests/test_demo.py::test_bad"
    assert first.location == "tests/test_demo.py:8"
    assert "assert 2 == 3" in first.message
    assert s.failures[1].location == "tests/test_demo.py:15"


def test_pytest_counts_errors_and_coverage() -> None:
    text = "TOTAL     120     12    90%\n=== 1 failed, 3 passed, 2 errors in 1.0s ==="
    s = testrun.parse_pytest(text)
    assert (s.passed, s.failed, s.errors, s.coverage) == (3, 1, 2, "90%") and not s.ok


def test_junit_xml() -> None:
    s = testrun.parse_junit((FIX / "pytest_junit.xml").read_text())
    assert (s.passed, s.failed, s.skipped) == (1, 2, 1)
    assert s.failures[0].name == "tests.test_demo::test_bad"
    assert s.failures[0].location == "tests/test_demo.py:8"
    assert s.failures[0].message.splitlines()[0] == "AssertionError: x should be 3"
    assert testrun.parse_junit("<not xml").total == 0


def test_jest_and_vitest() -> None:
    s = testrun.parse_jest(JEST)
    assert (s.passed, s.failed, s.skipped) == (6, 1, 1)
    assert s.failures[0].name == "math › adds numbers"
    assert s.failures[0].location == "src/sum.test.js:4"
    assert "toBe" in s.failures[0].message
    v = testrun.parse_jest(VITEST)
    assert (v.passed, v.failed) == (2, 1)


def test_go_test_json() -> None:
    s = testrun.parse_go_json((FIX / "go_test.json").read_text())
    assert (s.passed, s.failed, s.skipped) == (1, 1, 1)
    f = s.failures[0]
    assert f.name.endswith("TestBad") and f.location == "demo_test.go:8" and f.message == "got 2 want 3"


def test_cargo() -> None:
    s = testrun.parse_cargo(CARGO)
    assert (s.passed, s.failed, s.skipped) == (1, 1, 1)
    assert s.failures[0].name == "tests::bad" and s.failures[0].location == "src/lib.rs:12"
    assert "assertion" in s.failures[0].message


def test_ruff_json_and_format() -> None:
    findings = testrun.parse_ruff_json((FIX / "ruff.json").read_text())
    assert findings and findings[0].code == "F401" and findings[0].file == "bad.py" and findings[0].line == 1
    fmt = testrun.parse_ruff_format(
        "Would reformat: src/a.py\nWould reformat: b.py\n2 files would be reformatted\n"
    )
    assert [f.file for f in fmt] == ["src/a.py", "b.py"]
    new = "unformatted: File would be reformatted\n --> mod.py:2:1\n  |\n1 file would be reformatted\n"
    assert [(f.file, f.line) for f in testrun.parse_ruff_format(new)] == [("mod.py", 2)]
    concise = "mod.py:2:1: unformatted: File would be reformatted\n"
    assert [(f.file, f.line) for f in testrun.parse_ruff_format(concise)] == [("mod.py", 2)]
    assert testrun.parse_ruff_json("garbage") == []


def test_mypy_eslint_tsc() -> None:
    m = testrun.parse_mypy((FIX / "mypy.txt").read_text())
    assert len(m) == 1 and (m[0].file, m[0].line, m[0].code) == ("bad.py", 2, "assignment")
    e = testrun.parse_eslint_json(ESLINT)
    assert [(f.line, f.code) for f in e] == [(3, "no-unused-vars"), (9, "warning")]
    t = testrun.parse_tsc(TSC)
    assert [(f.file, f.line, f.code) for f in t] == [
        ("src/app.ts", 10, "TS2322"),
        ("src/util.ts", 2, "TS2304"),
    ]


def test_render_caps() -> None:
    s = testrun.TestSummary(
        failed=30, failures=[testrun.Failure(f"t{i}", "a.py:1", "boom") for i in range(30)]
    )
    text = testrun.render_tests(s, limit=5)
    assert "failures (5 of 30)" in text and "5. t4" in text and "6. t5" not in text
    many = [testrun.Finding("a.py", i, "E1", "x") for i in range(100)]
    out = testrun.render_findings("ruff check", many, exit_code=1, limit=10)
    assert "100 finding(s), first 10" in out and out.count("\n") == 10
    assert testrun.render_findings("mypy", [], exit_code=0) == "mypy: clean"


def test_plan_hash_ignores_status() -> None:
    a = [{"step": "Do x", "status": "pending", "done_when": "tests pass"}]
    b = [{"step": "Do x", "status": "completed", "done_when": "tests pass"}]
    assert testrun.plan_hash(a) == testrun.plan_hash(b)
    assert testrun.plan_hash(a) != testrun.plan_hash([{"step": "Do y", "status": "pending"}])
