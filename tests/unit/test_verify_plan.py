"""F2 `verify_plan` checks and the `exit_plan_mode` gate (`settings.plan_requires_verify`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import SessionState, ToolContext, ToolResult

GOOD = [
    {
        "step": "Add the parser in src/parse.py",
        "status": "pending",
        "done_when": "tests/test_parse.py passes",
    },
    {"step": "Run `uv run pytest -q`", "status": "pending"},
]


async def approve(questions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"responses": [{"answers": ["Approve"]}]}


async def call(services: Services, cwd: Path, state: SessionState, name: str, args: dict) -> ToolResult:
    ctx = ToolContext(
        session_id="s1", cwd=cwd, home=services.settings.home, state=state, interactive=True, question=approve
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    return out.result


async def plan(services: Services, cwd: Path, state: SessionState, steps: list[dict]) -> None:
    res = await call(services, cwd, state, "update_plan", {"steps": steps})
    assert not res.is_error, res.content


def test_manifest_and_schema(services: Services) -> None:
    m = services.harness.tools["verify_plan"].manifest
    assert m.exposure == "deferred" and m.is_read_only and m.default_action == "allow"
    item = services.harness.tools["update_plan"].manifest.parameters["properties"]["steps"]["items"]
    assert "done_when" in item["properties"] and item["required"] == ["step", "status"]


async def test_passes_good_plan(services: Services, workdir: Path) -> None:
    state = SessionState()
    await plan(services, workdir, state, GOOD)
    res = await call(services, workdir, state, "verify_plan", {})
    assert not res.is_error, res.content
    assert res.content.startswith("verify_plan: PASS") and res.meta["passed"]
    assert getattr(state, "plan_verified", None) == res.meta["hash"]


async def test_empty_plan_fails(services: Services, workdir: Path) -> None:
    res = await call(services, workdir, SessionState(), "verify_plan", {})
    assert res.is_error and "update_plan" in res.content


@pytest.mark.parametrize(
    ("steps", "needle"),
    [
        ([{"step": "Refactor the module", "status": "pending"}], "`command`) — step 1"),
        (
            [{"step": "Edit /etc/hosts", "status": "pending", "done_when": "resolves"}],
            "outside the working directory — step 1: /etc/hosts",
        ),
        (
            [{"step": "Copy ../secrets/a.txt here", "status": "pending", "done_when": "file exists"}],
            "outside the working directory",
        ),
        (
            [
                {"step": "Write docs", "status": "pending", "done_when": "README updated"},
                {"step": "write   docs", "status": "pending", "done_when": "again"},
            ],
            "step 2 repeats step 1",
        ),
        (
            [{"step": "Use postgres or sqlite?", "status": "pending", "done_when": "TBD"}],
            "unresolved questions — step 1",
        ),
    ],
)
async def test_failing_checks(services: Services, workdir: Path, steps: list[dict], needle: str) -> None:
    state = SessionState()
    await plan(services, workdir, state, steps)
    res = await call(services, workdir, state, "verify_plan", {})
    assert res.is_error and "verify_plan: FAIL" in res.content
    assert needle in res.content, res.content
    assert getattr(state, "plan_verified", None) is None


async def test_destructive_flagged_and_api_paths_ignored(services: Services, workdir: Path) -> None:
    state = SessionState()
    steps = [
        {"step": "Expose GET /api/events in the router", "status": "pending", "done_when": "curl works"},
        {
            "step": "Delete the old migration and deploy",
            "status": "pending",
            "done_when": "`make check` passes",
        },
        {"step": f"Update {workdir}/README.md", "status": "pending", "done_when": "renders"},
    ]
    await plan(services, workdir, state, steps)
    res = await call(services, workdir, state, "verify_plan", {})
    assert not res.is_error, res.content
    assert "needs approval when executed (destructive):" in res.content and "step 2" in res.content


async def test_exit_gate_off_by_default(services: Services, workdir: Path) -> None:
    res = await call(services, workdir, SessionState(plan_mode=True), "exit_plan_mode", {"plan": "do it"})
    assert not res.is_error, res.content


async def test_exit_gate_requires_verified_current_plan(home: Path, workdir: Path) -> None:
    svc = await Services.create(
        Settings(home=home, gateway="fake", plan_requires_verify=True), cwd=workdir, with_mcp=False
    )
    try:
        state = SessionState(plan_mode=True)
        res = await call(svc, workdir, state, "exit_plan_mode", {"plan": "p"})
        assert res.is_error and "verify_plan" in res.content and state.plan_mode
        await plan(svc, workdir, state, GOOD)
        res = await call(svc, workdir, state, "exit_plan_mode", {"plan": "p"})
        assert res.is_error and "verify_plan" in res.content  # recorded but not verified
        assert not (await call(svc, workdir, state, "verify_plan", {})).is_error
        # A content change through update_plan invalidates the verification.
        await plan(svc, workdir, state, [*GOOD, {"step": "Also `make lint`", "status": "pending"}])
        res = await call(svc, workdir, state, "exit_plan_mode", {"plan": "p"})
        assert res.is_error and "verify_plan" in res.content
        assert not (await call(svc, workdir, state, "verify_plan", {})).is_error
        # Status-only updates keep it.
        await plan(svc, workdir, state, [{**s, "status": "in_progress"} for s in state.plan])
        res = await call(svc, workdir, state, "exit_plan_mode", {"plan": "p"})
        assert not res.is_error, res.content
        assert not state.plan_mode
    finally:
        await svc.close()
