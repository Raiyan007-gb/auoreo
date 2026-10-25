"""skills-writer: `skill_manage` tool (create/update/delete/lint) + `auoreo skill lint`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.memory.skill_lint import lint, lint_file
from auoreo.tools.context import ToolContext

DESC = "Release a Python package to PyPI. Use when the user asks to publish, release or tag a version."
BODY = "# Release\n\n1. Bump the version.\n2. Run the tests.\n3. Tag and push; CI publishes.\n"


def ctx_for(svc: Services, workdir: Path, agent: str = "skills_writer") -> ToolContext:
    spec = build_spec(svc.harness, agent, "default", "manual")
    engine = PermissionEngine([Rule(permission="*", pattern="*", action="allow")], plans_dir=str(workdir))
    return ToolContext(
        session_id="s1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
    )


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result.content, out.result.is_error


def test_lint_rules() -> None:
    assert lint("release-python", DESC, BODY, "release-python") == []
    errors = lint("Bad_Name", "too short", "tiny", "other")
    assert len(errors) == 4
    assert any("kebab-case" in e for e in errors) and any("folder" in e for e in errors)
    assert any("description" in e for e in errors) and any("body" in e for e in errors)
    assert any("secret" in e for e in lint("x-y", DESC, BODY + "token ghp_abcdefghijklmnop1234"))
    assert lint("a--b", DESC, BODY) and lint("x" * 65, DESC, BODY)


async def test_create_reload_load_update_delete(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    assert "skill_manage" in {t.name for t in ctx.services["spec"].tools}
    mark = await services.events.last_seq()
    text, err = await call(
        services,
        ctx,
        "skill_manage",
        {
            "action": "create",
            "name": "release-python",
            "description": DESC,
            "body": BODY,
            "files": {"scripts/check.sh": "#!/bin/sh\nuv run pytest -q\n"},
        },
    )
    assert not err, text
    folder = services.settings.home / "skills" / "release-python"
    assert (folder / "scripts" / "check.sh").is_file()
    assert lint_file(folder / "SKILL.md") == []
    skill = services.harness.skills["release-python"]
    assert skill.source == "runtime" and skill.manifest.description == DESC
    events = [e for e in await services.events.replay(mark) if e.type == "harness.reloaded"]
    assert events and events[-1].data["skills"]["added"] == ["release-python"]

    # next turn: a fresh spec lists it and the `skill` tool loads it
    ctx2 = ctx_for(services, workdir, "default")
    text, err = await call(services, ctx2, "skill", {"name": "release-python"})
    assert not err and "Loaded skill" in text
    assert "Tag and push" in ctx2.state.pending_user_messages[-1]
    assert "scripts/check.sh" in ctx2.state.pending_user_messages[-1]

    text, err = await call(services, ctx, "skill_manage", {"action": "lint", "name": "release-python"})
    assert not err and text.endswith("ok")
    new_desc = DESC + " Also covers TestPyPI dry runs."
    text, err = await call(
        services, ctx, "skill_manage", {"action": "update", "name": "release-python", "description": new_desc}
    )
    assert not err, text
    assert services.harness.skills["release-python"].manifest.description == new_desc
    assert "Tag and push" in services.harness.skills["release-python"].body()  # body kept

    text, err = await call(services, ctx, "skill_manage", {"action": "delete", "name": "release-python"})
    assert not err and not folder.exists() and "release-python" not in services.harness.skills


async def test_rejections(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    base = {"action": "create", "name": "ok-skill", "description": DESC, "body": BODY}
    cases = [
        ({**base, "name": "Bad_Name"}, "kebab-case"),
        ({**base, "description": "short"}, "description"),
        ({**base, "body": "tiny"}, "body"),
        ({**base, "body": BODY + "\nexport KEY=sk-or-v1-abcdefabcdefabcdef\n"}, "secret"),
        ({**base, "files": {"../escape.txt": "x"}}, "inside the skill folder"),
        ({**base, "files": {"notes.md": "use ghp_abcdefghijklmnop1234"}}, "secret"),
        ({**base, "name": "research"}, "already exists"),  # bundled skill: no silent shadowing
        ({"action": "update", "name": "nope-skill", "body": BODY}, "use create"),
        ({"action": "delete", "name": "research"}, "only user skills"),
        ({"action": "lint", "name": "nope-skill"}, "unknown skill"),
        ({"action": "lint", "name": "x-y", "description": "short", "body": BODY}, "description"),
    ]
    for args, needle in cases:
        text, err = await call(services, ctx, "skill_manage", args)
        assert err and needle in text, (args, text)
    assert not (services.settings.home / "skills" / "ok-skill").exists()


def test_tool_asks_by_default(services: Services) -> None:
    m = services.harness.tools["skill_manage"].manifest
    assert m.permission == "skills" and m.default_action == "ask"


def test_cli_skill_lint(home: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["skill", "lint"])
    assert res.exit_code == 0, res.output
    assert "research" in res.output and "code-review" in res.output
    bad = home / "skills" / "broken-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: broken-skill\ndescription: too short\n---\nhi\n")
    res = runner.invoke(app, ["skill", "lint", "broken-skill"])
    assert res.exit_code == 1 and "description" in res.output
    assert runner.invoke(app, ["skill", "lint", "research"]).exit_code == 0
    assert runner.invoke(app, ["skill", "lint", "missing-skill"]).exit_code == 1
