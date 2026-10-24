from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.tools.context import ToolContext

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def ctx_for(svc: Services, workdir: Path, agent: str = "default", **kw: object) -> ToolContext:
    spec = build_spec(svc.harness, agent, "default", "manual")
    engine = PermissionEngine(list(ALLOW), plans_dir=str(workdir.resolve() / ".auoreo" / "plans"))
    return ToolContext(
        session_id="s1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
        **kw,
    )  # type: ignore[arg-type]


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    engine = ctx.services["engine"]
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)), tools=svc.harness.tools, ctx=ctx, engine=engine
    )
    return out.result.content, out.result.is_error


@pytest.fixture
def repo(workdir: Path) -> Path:
    (workdir / "src").mkdir()
    (workdir / "src" / "app.py").write_text("def main():\n    return 1\n\nclass Thing:\n    pass\n")
    (workdir / "tests").mkdir()
    (workdir / "tests" / "test_app.py").write_text("def test_main():\n    assert True\n")
    (workdir / "pyproject.toml").write_text("[project]\nname='x'\n")
    return workdir


async def test_glob_and_grep(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    text, err = await call(services, ctx, "glob", {"pattern": "**/*.py"})
    assert not err and "src/app.py" in text and "tests/test_app.py" in text
    text, err = await call(services, ctx, "grep", {"pattern": r"def \w+", "glob": "*.py"})
    assert "src/app.py:1:def main():" in text
    text, _ = await call(services, ctx, "grep", {"pattern": "nothing_here_xyz"})
    assert text == "no matches"
    text, _ = await call(services, ctx, "grep", {"pattern": "main", "output_mode": "files"})
    assert set(text.splitlines()) == {"src/app.py", "tests/test_app.py"}


async def test_codebase_map_lists_symbols_and_caches(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    text, err = await call(services, ctx, "codebase_map", {})
    assert not err and "src/app.py: main, Thing" in text and "pyproject.toml" in text
    assert "tests" in text
    assert list((services.settings.home / "cache" / "codebase_map").glob("*.txt"))


PATCH = """*** Begin Patch
*** Add File: docs/new.md
+# New
+hello
*** Update File: src/app.py
@@ def main
 def main():
-    return 1
+    return 2
*** Delete File: tests/test_app.py
*** End Patch"""


async def test_apply_patch_dry_run_then_apply(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    text, err = await call(services, ctx, "apply_patch", {"patch": PATCH, "dry_run": True})
    assert not err and "A docs/new.md" in text and (repo / "tests" / "test_app.py").exists()
    text, err = await call(services, ctx, "apply_patch", {"patch": PATCH})
    assert not err, text
    assert (repo / "docs" / "new.md").read_text() == "# New\nhello\n"
    assert "return 2" in (repo / "src" / "app.py").read_text()
    assert not (repo / "tests" / "test_app.py").exists()


async def test_apply_patch_is_all_or_nothing(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    bad = PATCH.replace("-    return 1", "-    return 99")
    text, err = await call(services, ctx, "apply_patch", {"patch": bad})
    assert err and "not found" in text
    assert not (repo / "docs" / "new.md").exists()  # nothing written
    text, err = await call(services, ctx, "apply_patch", {"patch": "not a patch"})
    assert err and "format" in text


async def test_apply_patch_move_and_secret_guard(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    move = (
        "*** Begin Patch\n*** Update File: src/app.py\n*** Move to: src/main.py\n"
        " def main():\n-    return 1\n+    return 3\n*** End Patch"
    )
    text, err = await call(services, ctx, "apply_patch", {"patch": move})
    assert not err and (repo / "src" / "main.py").exists() and not (repo / "src" / "app.py").exists()
    secret = "*** Begin Patch\n*** Add File: .env\n+KEY=x\n*** End Patch"
    text, err = await call(services, ctx, "apply_patch", {"patch": secret})
    assert err and "secret" in text and not (repo / ".env").exists()


async def test_todo_and_plan(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    text, err = await call(
        services,
        ctx,
        "todo",
        {"todos": [{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"}]},
    )
    assert not err and "(1/2 done)" in text and ctx.state.todos[1]["content"] == "b"
    text, err = await call(
        services,
        ctx,
        "todo",
        {"todos": [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]},
    )
    assert err
    text, err = await call(services, ctx, "update_plan", {"steps": [{"step": "x", "status": "pending"}]})
    assert not err and ctx.state.plan == [{"step": "x", "status": "pending"}]


async def test_plan_mode_blocks_edits_until_approved(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo, interactive=True)
    asked: list[list[dict]] = []  # type: ignore[type-arg]

    async def question(qs: list[dict]) -> dict:  # type: ignore[type-arg]
        asked.append(qs)
        return {"responses": [{"question": qs[0]["question"], "answers": ["Approve"]}], "timed_out": False}

    ctx.question = question
    await call(services, ctx, "enter_plan_mode", {})
    assert ctx.services["engine"].mode == "plan" and ctx.state.plan_mode
    text, err = await call(services, ctx, "write", {"path": "x.txt", "content": "nope"})
    assert err and "plan mode" in text
    text, err = await call(services, ctx, "write", {"path": ".auoreo/plans/p.md", "content": "plan"})
    assert not err
    text, err = await call(services, ctx, "exit_plan_mode", {"plan": "1. do it"})
    assert not err and ctx.services["engine"].mode == "manual" and not ctx.state.plan_mode
    assert asked[0][0]["plan"] == "1. do it"


async def test_interactive_only_tools_are_denied_headless(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo, interactive=False)
    text, err = await call(services, ctx, "ask_user_question", {"questions": [{"question": "?"}]})
    assert err and "non-interactive" in text


async def test_skill_injects_user_message_into_next_request(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("skill", {"name": "code-review"})]), Scripted(text="reviewed"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "review my diff", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    second = gateway.requests[1].messages
    assert second[-1]["role"] == "user" and second[-1]["content"].startswith('<skill name="code-review">')
    assert "# Code review" in second[-1]["content"]


async def test_tool_search_loads_deferred_tool_for_next_step(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("tool_search", {"query": "call a REST API"})]), Scripted(text="ok"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "find an http tool", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    first = {t["function"]["name"] for t in gateway.requests[0].tools}
    second = {t["function"]["name"] for t in gateway.requests[1].tools}
    assert "http_request" not in first and "http_request" in second


async def test_memory_tool_batch_and_guards(services: Services, repo: Path) -> None:
    ctx = ctx_for(services, repo)
    text, err = await call(
        services,
        ctx,
        "memory",
        {
            "operations": [
                {"action": "add", "target": "fact", "content": "Deploys go out on Fridays"},
                {"action": "add", "target": "user", "content": "Prefers short answers"},
            ]
        },
    )
    assert not err, text
    assert "Prefers short answers" in (services.settings.home / "memory" / "USER.md").read_text()
    text, err = await call(
        services,
        ctx,
        "memory",
        {
            "operations": [
                {
                    "action": "replace",
                    "target": "fact",
                    "old_text": "Fridays",
                    "content": "Deploys go out on Mondays",
                }
            ]
        },
    )
    assert not err
    hits, _ = await call(services, ctx, "knowledge_search", {"query": "when do deploys go out"})
    assert "Mondays" in hits and "[fact:" in hits
    text, err = await call(
        services,
        ctx,
        "memory",
        {
            "operations": [
                {"action": "add", "target": "fact", "content": "key is sk-or-v1-abcdefabcdefabcdef"}
            ]
        },
    )
    assert err and "secret" in text
    big = "x" * 3000
    text, err = await call(
        services,
        ctx,
        "memory",
        {
            "operations": [
                {"action": "add", "target": "user", "content": big[:1999]},
                {"action": "add", "target": "user", "content": big[:1999]},
            ]
        },
    )
    assert err and "limit" in text
    assert big[:100] not in (services.settings.home / "memory" / "USER.md").read_text()  # atomic


async def test_session_search_finds_past_messages(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "the launch codename is bluebird", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    ctx = ctx_for(services, Path("."))
    text, _ = await call(services, ctx, "session_search", {"query": "launch codename"})
    assert "bluebird" in text and f"session:{sess.id}" in text


async def test_ask_user_question_round_trip_through_broker(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(
        Scripted(
            tool_calls=[
                (
                    "ask_user_question",
                    {
                        "questions": [
                            {"question": "Which db?", "options": [{"label": "sqlite"}, {"label": "postgres"}]}
                        ]
                    },
                )
            ]
        ),
        Scripted(text="using sqlite"),
    )
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "set up db", interactive=True)
    for _ in range(100):
        if services.questions.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    q = services.questions.pending(sess.id)[0]
    assert await services.questions.answer(q.request_id, [{"question": "Which db?", "answers": ["sqlite"]}])
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert "sqlite" in hist[2]["content"]


async def test_plan_then_build_workflow(services: Services, gateway: FakeGateway, repo: Path) -> None:
    gateway.queue(
        Scripted(text="PLAN: change main to return 2"),
        Scripted(tool_calls=[("read", {"path": "src/app.py"})]),
        Scripted(text="implemented"),
    )
    sess = await services.create_session(workflow="plan-then-build")
    rid = await services.coordinator.submit(sess.id, "make main return 2", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed" and result.text == "implemented"
    planner_tools = {t["function"]["name"] for t in gateway.requests[0].tools}
    assert "write" not in planner_tools and "bash" not in planner_tools and "read" in planner_tools
    assert "PLAN: change main" in gateway.requests[1].messages[-1]["content"]
