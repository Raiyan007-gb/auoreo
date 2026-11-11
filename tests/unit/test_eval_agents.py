"""M7 W3 wave 2, phase C: agent evals (`agents/<name>/evals/*.yaml`, `auoreo eval agents`).

On the fake gateway a case checks an agent profile's contract (its tool list, permission and
unattended outcomes), never model quality. An `evals/` folder under an agent must not break
`harness validate`."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from tests.conftest import REPO, write

PROBE = """---
name: probe
description: eval probe
tools:
  allow: [read, tool_search, delegate_task]
skills: []
max_steps: 5
memory: false
---
You are a probe.
"""
PASS = """description: the profile and a scripted read
input: look at the readme
script:
  - tool_calls: [{name: read, args: {path: README.md}}]
  - text: "done reading"
expect:
  tools_available: [read, tool_search, delegate_task]
  tools_called: [read]
  final_matches: "^done"
  events: [tool.completed]
  max_steps: 2
"""
UNATTENDED = """description: a webhook run cannot delegate
input: delegate it
unattended: webhook
script:
  - tool_calls: [{name: delegate_task, args: {task: x}}]
  - text: "could not"
expect:
  tools_available: [read, tool_search]
  tools_denied: [delegate_task]
"""
FAIL = """description: someone widened the profile
input: hi
expect:
  tools_available: [read]
"""


def load() -> ModuleType:
    path = REPO / "scripts" / "eval_agents.py"
    spec = importlib.util.spec_from_file_location("auoreo_eval_agents_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    write(r / "agents" / "probe" / "AGENT.md", PROBE)
    write(r / "agents" / "probe" / "evals" / "reads.yaml", PASS)
    write(r / "agents" / "probe" / "evals" / "unattended.yaml", UNATTENDED)
    write(r / "agents" / "wide" / "AGENT.md", PROBE.replace("name: probe", "name: wide"))
    write(r / "agents" / "wide" / "evals" / "allowlist.yaml", FAIL)
    return r


def test_passing_cases_exit_zero(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["probe", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "probe" in out and "reads" in out and "unattended" in out and "PASS" in out and "FAIL" not in out


def test_failing_case_names_the_unexpected_tool(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["wide", "--root", str(root), "--json"]) == 1
    results = json.loads(capsys.readouterr().out)
    assert [(r["agent"], r["case"], r["ok"]) for r in results] == [("wide", "allowlist", False)]
    assert any("unexpected tool" in f and "tool_search" in f for f in results[0]["failures"])


def test_json_reports_calls_and_denials(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["probe", "--root", str(root), "--json"]) == 0
    by_case = {r["case"]: r for r in json.loads(capsys.readouterr().out)}
    assert by_case["reads"]["called"] == ["read"] and by_case["reads"]["steps"] == 2
    assert by_case["unattended"]["denied"] == ["delegate_task"]


def test_unknown_agent_and_malformed_case_exit_two(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert load().main(["ghost", "--root", str(root)]) == 2
    write(root / "agents" / "probe" / "evals" / "bad.yaml", "input: x\nexpect: {tools_avail: [read]}\n")
    assert load().main(["probe", "--root", str(root)]) == 2
    assert "bad" in capsys.readouterr().err


def test_bundled_cases_pass(capsys: pytest.CaptureFixture[str]) -> None:
    """The shipped cases (triage, researcher) hold on the fake gateway; no CI gate beyond this."""
    assert load().main(["triage", "researcher"]) == 0, capsys.readouterr()


def test_cli_wraps_the_script(root: Path) -> None:
    r = CliRunner().invoke(app, ["eval", "agents", "wide", "--root", str(root)])
    assert r.exit_code == 1 and "unexpected tool" in r.output


def test_harness_validate_tolerates_an_evals_folder(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    write(project / ".auoreo" / "agents" / "probe" / "AGENT.md", PROBE)
    write(project / ".auoreo" / "agents" / "probe" / "evals" / "reads.yaml", PASS)
    monkeypatch.chdir(project)
    r = CliRunner().invoke(app, ["harness", "validate"])
    assert r.exit_code == 0, r.output
    assert "harness valid" in r.output and "!" not in r.output
