"""Engine side of `run_tests` / `run_lint` (spec L112): pure output parsers that turn
pytest / junit XML / jest·vitest / `go test -json` / cargo / ruff / mypy / eslint / tsc
output into compact summaries, plus one helper that runs an argv through the sandbox.

Parsers never raise on odd input: an unparseable run yields an empty summary and the
handler falls back to the raw output tail.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.tools.context import ToolContext

MAX_FAILURES = 10
MAX_FINDINGS = 40
MAX_MSG_LINES = 4


@dataclass
class Failure:
    name: str
    location: str = ""
    message: str = ""


@dataclass
class TestSummary:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    failures: list[Failure] = field(default_factory=list)
    coverage: str | None = None

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0


@dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str
    col: int = 0

    def render(self) -> str:
        where = f"{self.file}:{self.line}" + (f":{self.col}" if self.col else "")
        return f"{where} {self.code} {self.message}".replace("  ", " ").strip()


# --- pytest -------------------------------------------------------------------------

_PY_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")
_PY_FINAL = re.compile(r"^=*\s*(?:\d+ \w+(?:, )?)+.* in [\d.]+s", re.M)
_PY_SECTION = re.compile(r"^_{3,} (?P<name>.+?) _{3,}$", re.M)
_PY_LOC = re.compile(r"^(?P<loc>[^\s:]+\.py:\d+)(?::| )", re.M)
_PY_SHORT = re.compile(r"^(FAILED|ERROR) (?P<id>\S+)(?: - (?P<msg>.*))?$", re.M)
_COV_TOTAL = re.compile(r"^TOTAL\s+.*?(\d+(?:\.\d+)?%)\s*$", re.M)


def _counts(line: str, s: TestSummary) -> None:
    for n, kind in _PY_COUNT.findall(line):
        key = {"passed": "passed", "failed": "failed", "skipped": "skipped", "xfailed": "skipped"}.get(kind)
        if kind.startswith("error"):
            key = "errors"
        if key:
            setattr(s, key, getattr(s, key) + int(n))


def parse_pytest(text: str) -> TestSummary:
    """`pytest -q -rfE --tb=short` output: final counts line, failure sections, short summary."""
    s = TestSummary()
    finals = _PY_FINAL.findall(text)
    if finals:
        _counts(finals[-1], s)
    sections = {m.group("name"): m.end() for m in _PY_SECTION.finditer(text)}
    for m in _PY_SHORT.finditer(text):
        test_id = m.group("id")
        f = Failure(test_id, message=(m.group("msg") or "").strip())
        short = test_id.split("::")[-1]
        for name, start in sections.items():
            if name.replace(".", "::").endswith(short) or name.endswith(short):
                body = text[start : _next_section(text, start)]
                locs = _PY_LOC.findall(body)
                f.location = locs[-1] if locs else ""
                elines = [ln[1:].strip() for ln in body.splitlines() if ln.startswith("E ")]
                if elines:
                    f.message = "\n".join(elines[:MAX_MSG_LINES])
                break
        s.failures.append(f)
    if cov := _COV_TOTAL.search(text):
        s.coverage = cov.group(1)
    return s


def _next_section(text: str, start: int) -> int:
    m = re.compile(r"^(_{3,} .+ _{3,}|={3,}.*={3,})$", re.M).search(text, start)
    return m.start() if m else len(text)


def parse_junit(xml_text: str) -> TestSummary:
    """JUnit XML (pytest `--junitxml`, jest-junit, go-junit-report)."""
    s = TestSummary()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return s
    for case in root.iter("testcase"):
        bad = case.find("failure")
        err = case.find("error")
        if bad is None and err is not None:
            bad = err
        if bad is not None:
            if bad is err:
                s.errors += 1
            else:
                s.failed += 1
            cls = case.get("classname", "")
            name = f"{cls}::{case.get('name', '')}" if cls else case.get("name", "")
            body = bad.text or ""
            locs = _PY_LOC.findall(body)
            loc = locs[-1] if locs else (f"{case.get('file')}:{case.get('line')}" if case.get("file") else "")
            elines = [ln[1:].strip() for ln in body.splitlines() if ln.startswith("E ")]
            msg = "\n".join(elines[:MAX_MSG_LINES]) or (bad.get("message") or "").strip()
            s.failures.append(Failure(name, loc, msg))
        elif case.find("skipped") is not None:
            s.skipped += 1
        else:
            s.passed += 1
    return s


# --- jest / vitest ------------------------------------------------------------------

_JEST_TESTS = re.compile(r"^Tests:\s+(.*)$", re.M)
_VITEST_TESTS = re.compile(r"^\s*Tests\s+(.*?)(?:\s+\(\d+\))?\s*$", re.M)
_JEST_BLOCK = re.compile(r"^\s*● (?P<name>.+?)\s*$", re.M)
_JS_LOC = re.compile(r"\(?(?P<file>[^\s()]+\.(?:[cm]?[jt]sx?)):(?P<line>\d+):\d+\)?")


def parse_jest(text: str) -> TestSummary:
    """jest (`Tests: 1 failed, 4 passed, 5 total`) or vitest (`Tests  1 failed | 4 passed`)."""
    s = TestSummary()
    m = _JEST_TESTS.search(text) or _VITEST_TESTS.search(text)
    if m:
        _counts(m.group(1).replace("|", ","), s)
        s.skipped += sum(int(n) for n in re.findall(r"(\d+) (?:todo|pending)", m.group(1)))
    blocks = list(_JEST_BLOCK.finditer(text))
    for i, b in enumerate(blocks):
        name = b.group("name")
        if name.startswith("Test suite failed") and len(blocks) > 1:
            continue
        body = text[b.end() : blocks[i + 1].start() if i + 1 < len(blocks) else len(text)]
        loc = _JS_LOC.search(body)
        lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("at ")]
        s.failures.append(
            Failure(
                name,
                f"{loc.group('file')}:{loc.group('line')}" if loc else "",
                "\n".join(lines[:MAX_MSG_LINES]),
            )
        )
    return s


# --- go test -json ------------------------------------------------------------------

_GO_LOC = re.compile(r"^\s+(?P<loc>[\w./-]+_test\.go:\d+): (?P<msg>.*)$")


def parse_go_json(text: str) -> TestSummary:
    s = TestSummary()
    output: dict[str, list[str]] = {}
    for raw in text.splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or not ev.get("Test"):
            continue
        key = f"{ev.get('Package', '')}.{ev['Test']}"
        action = ev.get("Action")
        if action == "output":
            output.setdefault(key, []).append(str(ev.get("Output", "")))
            continue
        counter = {"pass": "passed", "fail": "failed", "skip": "skipped"}.get(str(action))
        if counter:
            setattr(s, counter, getattr(s, counter) + 1)
        if action == "fail":
            f = Failure(key)
            for line in output.get(key, []):
                if m := _GO_LOC.match(line.rstrip("\n")):
                    f.location, f.message = m.group("loc"), m.group("msg")
                    break
            s.failures.append(f)
    return s


# --- cargo test ---------------------------------------------------------------------

_CARGO_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored")
_CARGO_SECTION = re.compile(r"^---- (?P<name>\S+) stdout ----$", re.M)
_CARGO_PANIC = re.compile(r"panicked at (?P<loc>[^\s:]+:\d+)(?::\d+)?:?\s*\n?(?P<msg>.*)")


def parse_cargo(text: str) -> TestSummary:
    s = TestSummary()
    for p, f, ign in _CARGO_RESULT.findall(text):
        s.passed += int(p)
        s.failed += int(f)
        s.skipped += int(ign)
    for m in _CARGO_SECTION.finditer(text):
        body = text[m.end() : m.end() + 2000]
        panic = _CARGO_PANIC.search(body)
        s.failures.append(
            Failure(
                m.group("name"),
                panic.group("loc") if panic else "",
                panic.group("msg").strip() if panic else "",
            )
        )
    return s


# --- linters ------------------------------------------------------------------------

_MYPY = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)? error: (?P<msg>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$",
    re.M,
)
_TSC = re.compile(
    r"^(?P<file>[^(\n]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$", re.M
)
# ruff < 0.15 prints `Would reformat: f`; newer prints a diagnostic (` --> f:2:1`, or concise
# `f:2:1: unformatted: …`).
_REFORMAT = re.compile(
    r"^(?:Would reformat: (?P<old>.+)|\s*--> (?P<arrow>[^\n]+?):(?P<line>\d+):\d+"
    r"|(?P<concise>[^\s:][^:\n]*):(?P<cline>\d+):\d+: unformatted\b.*)$",
    re.M,
)


def parse_ruff_json(text: str) -> list[Finding]:
    try:
        items = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        loc = it.get("location") or {}
        out.append(
            Finding(
                str(it.get("filename", "")),
                int(loc.get("row", 0)),
                str(it.get("code") or ""),
                str(it.get("message", "")),
                int(loc.get("column", 0)),
            )
        )
    return out


def parse_ruff_format(text: str) -> list[Finding]:
    out = []
    for m in _REFORMAT.finditer(text):
        file = (m.group("old") or m.group("arrow") or m.group("concise") or "").strip()
        out.append(Finding(file, int(m.group("line") or m.group("cline") or 0), "format", "would reformat"))
    return out


def parse_mypy(text: str) -> list[Finding]:
    return [
        Finding(
            m.group("file"),
            int(m.group("line")),
            m.group("code") or "error",
            m.group("msg"),
            int(m.group("col") or 0),
        )
        for m in _MYPY.finditer(text)
    ]


def parse_eslint_json(text: str) -> list[Finding]:
    try:
        files = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for f in files if isinstance(files, list) else []:
        for msg in f.get("messages", []):
            sev = "error" if msg.get("severity") == 2 else "warning"
            out.append(
                Finding(
                    str(f.get("filePath", "")),
                    int(msg.get("line") or 0),
                    str(msg.get("ruleId") or sev),
                    str(msg.get("message", "")),
                    int(msg.get("column") or 0),
                )
            )
    return out


def parse_tsc(text: str) -> list[Finding]:
    return [
        Finding(
            m.group("file").strip(),
            int(m.group("line")),
            m.group("code"),
            m.group("msg"),
            int(m.group("col")),
        )
        for m in _TSC.finditer(text)
    ]


# --- rendering ----------------------------------------------------------------------


def render_tests(s: TestSummary, *, limit: int = MAX_FAILURES) -> str:
    verdict = "PASSED" if s.ok else "FAILED"
    lines = [
        f"result: {verdict} — {s.passed} passed, {s.failed} failed, {s.skipped} skipped, {s.errors} errors"
    ]
    if s.failures:
        lines.append(f"failures ({min(limit, len(s.failures))} of {len(s.failures)}):")
        for i, f in enumerate(s.failures[:limit], 1):
            lines.append(f"{i}. {f.name}" + (f"  @ {f.location}" if f.location else ""))
            lines += [f"   {ln}" for ln in f.message.splitlines()[:MAX_MSG_LINES] if ln.strip()]
    if s.coverage:
        lines.append(f"coverage: {s.coverage}")
    return "\n".join(lines)


def render_findings(tool: str, findings: list[Finding], *, exit_code: int, limit: int = MAX_FINDINGS) -> str:
    if not findings:
        state = "clean" if exit_code == 0 else f"failed (exit {exit_code}, no parsed findings; see output)"
        return f"{tool}: {state}"
    head = f"{tool}: {len(findings)} finding(s)" + (f", first {limit}" if len(findings) > limit else "")
    return "\n".join([head, *(f"  {f.render()}" for f in findings[:limit])])


def tail(text: str, n: int = 60) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


# --- plans (verify_plan gate) ---------------------------------------------------------


def plan_hash(plan: list[dict[str, Any]]) -> str:
    """Content hash of a plan (`step` + `done_when`; status changes keep the hash) that
    `verify_plan` records and `exit_plan_mode` compares when `plan_requires_verify` is set."""
    core = [[str(s.get("step", "")).strip(), str(s.get("done_when", "")).strip()] for s in plan]
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()[:16]


# --- execution ----------------------------------------------------------------------


@dataclass
class RunOutput:
    argv: list[str]
    exit_code: int
    output: str
    seconds: float
    timed_out: bool = False


async def execute(
    ctx: ToolContext, argv: list[str], *, timeout_s: float, env: dict[str, str] | None = None
) -> RunOutput:
    """Run one argv in `ctx.cwd` through the sandbox backend (stderr merged)."""
    from auoreo.backends.sandbox import ExecSpec, SandboxError
    from auoreo.tools.context import ToolError

    policy = ctx.exec_policy
    spec = ExecSpec(
        argv=argv,
        cwd=ctx.cwd,
        env={**ctx.env, **(env or {})},
        timeout_s=timeout_s,
        mode=policy.mode,  # type: ignore[arg-type]
        net=policy.net,  # type: ignore[arg-type]
        merge_stderr=True,
    )
    start = time.monotonic()
    try:
        res = await ctx.backend("sandbox").run(spec)
    except SandboxError as exc:
        raise ToolError(f"[sandbox: {exc}]") from exc
    return RunOutput(argv, res.exit_code, res.stdout + res.stderr, time.monotonic() - start, res.timed_out)
