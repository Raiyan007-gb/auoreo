"""I4 §9.3 ralph-lite: workflow `loop: {until, max_iterations}` steps + bundled ralph_lite."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.harness.manifests import WorkflowLoop, WorkflowManifest
from auoreo.runtime.runner import TurnResult
from auoreo.runtime.workflow_loop import until_met
from tests.conftest import write


def test_loop_validation_and_checks() -> None:
    with pytest.raises(ValidationError):
        WorkflowLoop(until="([unclosed")
    with pytest.raises(ValidationError):
        WorkflowLoop(until="check:shell rm -rf /")
    loop = WorkflowLoop(until="(?m)^DONE$", max_iterations=3)
    assert until_met(loop, TurnResult("r", "completed", "work\nDONE"))
    assert not until_met(loop, TurnResult("r", "completed", "not DONE yet"))
    assert until_met(WorkflowLoop(until="check:json"), TurnResult("r", "completed", '{"ok": 1}'))
    assert not until_met(WorkflowLoop(until="check:nonempty"), TurnResult("r", "completed", "  "))
    wf = WorkflowManifest.model_validate(
        {"name": "x", "description": "d", "steps": [{"id": "a", "agent": "default", "loop": {"until": "ok"}}]}
    )
    assert wf.steps[0].loop is not None and wf.steps[0].loop.max_iterations == 5


async def _events(services: Services, sid: str, kind: str) -> list[dict]:
    events = await services.events.replay(0, frozenset({f"session:{sid}"}), limit=10_000)
    return [e.data for e in events if e.type == kind]


async def test_bundled_ralph_lite_loops_until_done(services: Services, gateway: FakeGateway) -> None:
    assert "ralph_lite" in services.harness.workflows
    gateway.queue(
        Scripted(text="did part one"), Scripted(text="did part two"), Scripted(text="verified\nDONE")
    )
    sess = await services.create_session()
    run_id = await services.coordinator.submit(
        sess.id, "build the thing", workflow="ralph_lite", interactive=False
    )
    await services.coordinator.wait(sess.id, 20)
    result = services.coordinator.result(run_id or "")
    assert result is not None and result.status == "completed" and result.text.endswith("DONE")
    its = await _events(services, sess.id, "workflow.loop.iteration")
    assert [e["iteration"] for e in its] == [1, 2, 3] and its[-1]["met"] is True
    users = [r for r in gateway.requests if not r.metadata.get("purpose")]
    last_prompt = str(users[-1].messages[-1]["content"])
    assert "iteration 3" in last_prompt and "did part two" in last_prompt


async def test_loop_stops_at_max_iterations_in_child_steps(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    write(
        workdir / ".auoreo" / "workflows" / "two.yaml",
        "name: two\ndescription: d\nsteps:\n  - id: a\n    agent: default\n"
        "    input: 'try {{loop.iteration}}: {{input}}'\n    loop: {until: 'FINISHED', max_iterations: 2}\n"
        "  - id: b\n    agent: default\n    input: 'after: {{steps.a.output}}'\n",
    )
    services.harness.reload()
    gateway.queue(Scripted(text="nope"), Scripted(text="still nope"), Scripted(text="b ran"))
    sess = await services.create_session()
    run_id = await services.coordinator.submit(sess.id, "go", workflow="two", interactive=False)
    await services.coordinator.wait(sess.id, 20)
    its = await _events(services, sess.id, "workflow.loop.iteration")
    assert len(its) == 2 and not its[-1]["met"]
    steps = await _events(services, sess.id, "workflow.step.completed")
    assert steps[0]["status"] == "max_steps" and steps[1]["output"] == "b ran"
    assert services.coordinator.result(run_id or "") is not None


# ------------------------------------------------------------------ M7: check:files_exist
def test_files_exist_check_validation_and_semantics(tmp_path: Path) -> None:
    for bad in ("check:files_exist", "check:files_exist:", "check:files_exist:/etc/passwd",
                "check:files_exist:../x", "check:files_exist:a,,b", "check:files_exist:C:/x"):  # fmt: skip
        with pytest.raises(ValidationError):
            WorkflowLoop(until=bad)
    loop = WorkflowLoop(until="check:files_exist:out/report.md,done.flag")
    ok = TurnResult("r", "completed", "anything")
    assert not until_met(loop, ok, tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.md").write_text("r")
    assert not until_met(loop, ok, tmp_path)
    (tmp_path / "done.flag").write_text("")
    assert until_met(loop, ok, tmp_path)
    assert not until_met(loop, ok)  # no workspace known: never met


async def test_files_exist_loop_stops_when_the_file_appears(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    write(
        workdir / ".auoreo" / "workflows" / "fx.yaml",
        "name: fx\ndescription: d\nsteps:\n  - id: a\n    agent: default\n"
        "    loop: {until: 'check:files_exist:DONE.txt', max_iterations: 3}\n",
    )
    services.harness.reload()
    gateway.queue(Scripted(text="one"), Scripted(text="two"), Scripted(text="three"))
    sess = await services.create_session()

    async def make_file_after_first() -> None:
        for _ in range(1000):
            if await _events(services, sess.id, "workflow.loop.iteration"):
                break
            await asyncio.sleep(0.01)
        (workdir / "DONE.txt").write_text("ok")

    maker = asyncio.create_task(make_file_after_first())
    await services.coordinator.submit(sess.id, "go", workflow="fx", interactive=False)
    await services.coordinator.wait(sess.id, 20)
    await maker
    its = await _events(services, sess.id, "workflow.loop.iteration")
    assert its[-1]["met"] is True and len(its) <= 2
