"""F2 `run_tests` / `run_lint` end to end on tiny temp projects (repo venv's pytest/ruff)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext

ALLOW = [
    Rule(permission="run_tests", pattern="*", action="allow"),
    Rule(permission="run_lint", pattern="*", action="allow"),
]

TESTS = """
import pytest

def test_ok():
    assert 1 == 1

def test_bad():
    value = 2
    assert value == 3

@pytest.mark.skip(reason="later")
def test_skip():
    pass
"""


@pytest.fixture(autouse=True)
def _venv_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def project(workdir: Path) -> Path:
    (workdir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n\n[tool.pytest.ini_options]\n'
        'testpaths = ["tests"]\n\n[tool.ruff]\nline-length = 100\n'
    )
    (workdir / "tests").mkdir()
    (workdir / "tests" / "test_demo.py").write_text(TESTS)
    return workdir


async def call(services: Services, cwd: Path, name: str, args: dict, rules: list[Rule] | None = None):
    ctx = ToolContext(session_id="s1", cwd=cwd, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW if rules is None else rules),
    )
    return out.result


def test_manifests(services: Services) -> None:
    for name in ("run_tests", "run_lint"):
        m = services.harness.tools[name].manifest
        assert m.exposure == "deferred" and m.sandbox_mode == "workspace-write" and m.default_action == "ask"


async def test_run_tests_summarizes_pytest(services: Services, project: Path) -> None:
    res = await call(services, project, "run_tests", {})
    assert res.is_error
    text = res.content
    assert "pytest -q" in text.splitlines()[0]
    assert "result: FAILED — 1 passed, 1 failed, 1 skipped, 0 errors" in text
    assert "test_bad" in text and "tests/test_demo.py:" in text and "assert 2 == 3" in text
    assert res.meta["runner"] == "pytest" and res.meta["failed"] == 1
    assert not list((project / ".pytest_cache").glob("auoreo-junit-*.xml"))  # cleaned up


async def test_run_tests_filter_passes(services: Services, project: Path) -> None:
    res = await call(services, project, "run_tests", {"k": "test_ok", "path": "tests"})
    assert not res.is_error, res.content
    assert "result: PASSED — 1 passed, 0 failed" in res.content


async def test_run_tests_guards(services: Services, project: Path, workdir: Path, tmp_path: Path) -> None:
    res = await call(services, project, "run_tests", {"path": "../elsewhere"})
    assert res.is_error and "outside the working directory" in res.content
    res = await call(services, project, "run_tests", {"k": "-x"})
    assert res.is_error and "must not start" in res.content
    empty = tmp_path / "empty_proj"
    empty.mkdir()
    res = await call(services, empty, "run_tests", {})
    assert res.is_error and "no test runner detected" in res.content
    res = await call(services, project, "run_tests", {}, rules=[])
    assert res.is_error and "needs approval" in res.content


async def test_run_lint_ruff(services: Services, project: Path) -> None:
    (project / "mod.py").write_text("import os\nx=1\n")
    res = await call(services, project, "run_lint", {"tools": ["ruff"]})
    assert res.is_error
    assert "ruff check:" in res.content and "mod.py:1:8 F401" in res.content
    assert "ruff format:" in res.content and "mod.py:" in res.content and str(project) not in res.content
    (project / "mod.py").write_text("x = 1\n")
    res = await call(services, project, "run_lint", {"paths": ["mod.py"]})
    assert not res.is_error, res.content
    assert res.content.startswith("lint: clean") and "ruff check: clean" in res.content


async def test_run_lint_nothing_configured(services: Services, tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    res = await call(services, bare, "run_lint", {})
    assert res.is_error and "no linters configured" in res.content
