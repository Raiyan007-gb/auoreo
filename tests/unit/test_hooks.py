"""Hooks engine (M5c): real subprocess hooks in a project overlay."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.sessions_ops import set_archived
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.runtime.hooks import HookRunner, hooks_of, matches
from auoreo.schema.events import ServerEvent
from auoreo.tools.context import ToolContext
from tests.conftest import write

ALLOW_ALL = [Rule(permission="*", pattern="*", action="allow")]
FILLER = "lorem ipsum dolor sit amet " * 11


def hook(
    workdir: Path,
    name: str,
    event: str,
    script: str,
    *,
    matcher: str = "*",
    on_error: str = "warn",
    timeout_ms: int = 5000,
) -> None:
    root = workdir / ".auoreo"
    body = "import json, sys, os, time\npayload = json.load(sys.stdin)\n" + script
    write(root / "scripts" / f"{name}.py", body)
    write(
        root / "hooks" / f"{name}.yaml",
        f"name: {name}\ndescription: test hook\nevent: {event}\nmatcher: '{matcher}'\n"
        f"command: ['{sys.executable}', './scripts/{name}.py']\n"
        f"timeout_ms: {timeout_ms}\non_error: {on_error}\n",
    )


def reply(obj: dict[str, Any]) -> str:
    return f"print(json.dumps({obj!r}))\n"


async def events(svc: Services, sid: str, kind: str | None = None) -> list[ServerEvent]:
    out = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [e for e in out if kind is None or e.type == kind]


async def call_tool(
    svc: Services, sid: str, name: str, args: dict[str, Any], workdir: Path
) -> tuple[str, bool]:
    ctx = ToolContext(session_id=sid, cwd=workdir, home=svc.settings.home, harness=svc.harness)
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(list(ALLOW_ALL)),
    )
    return out.result.content, out.result.is_error


async def run_turn(svc: Services, text: str) -> tuple[str, str]:
    sess = await svc.create_session()
    rid = await svc.coordinator.submit(sess.id, text, interactive=False)
    await svc.coordinator.wait(sess.id, 20)
    res = svc.coordinator.result(rid or "")
    assert res is not None
    return sess.id, res.status


def test_matcher_alternatives() -> None:
    assert matches("edit|write", "write") and matches("*", "x") and matches("mcp__*", "mcp__a__b")
    assert not matches("edit|write", "read")


async def test_extension_registered(services: Services) -> None:
    assert isinstance(hooks_of(services), HookRunner)


# ------------------------------------------------------------------ PreToolUse
async def test_pre_tool_use_deny(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    hook(
        workdir,
        "no_a",
        "PreToolUse",
        "if payload['arguments']['path'] == 'a.txt' and payload['tool'] == 'read':\n"
        "    print(json.dumps({'decision': 'deny', 'message': 'a.txt is off limits'}))\n",
        matcher="read|edit",
    )
    services.harness.reload()
    sess = await services.create_session()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert err and "a.txt is off limits" in text
    ran = await events(services, sess.id, "hook.ran")
    assert [(e.data["name"], e.data["event"], e.data["decision"]) for e in ran] == [
        ("no_a", "PreToolUse", "deny")
    ]
    assert isinstance(ran[0].data["ms"], int)
    async with services.db.session() as s:
        rows = list((await s.execute(select(AuditRow).where(AuditRow.action == "hook.deny"))).scalars())
    assert rows and rows[0].detail["message"] == "a.txt is off limits"


async def test_pre_tool_use_modify_and_revalidate(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("AAA")
    (workdir / "b.txt").write_text("BBB")
    hook(workdir, "redirect", "PreToolUse", reply({"decision": "modify", "input": {"path": "b.txt"}}))
    services.harness.reload()
    sess = await services.create_session()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert not err and "BBB" in text
    # an invalid modification fails the schema check again
    hook(workdir, "redirect", "PreToolUse", reply({"decision": "modify", "input": {"nope": 1}}))
    services.harness.reload()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert err and "a PreToolUse hook produced invalid arguments" in text


async def test_empty_stdout_allows_and_matcher_filters(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    hook(workdir, "quiet", "PreToolUse", "")
    hook(workdir, "other", "PreToolUse", reply({"decision": "deny"}), matcher="bash")
    services.harness.reload()
    sess = await services.create_session()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert not err and "A" in text
    ran = await events(services, sess.id, "hook.ran")
    assert [(e.data["name"], e.data["decision"]) for e in ran] == [("quiet", "allow")]


async def test_post_tool_use_payload(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("hello")
    dump = workdir / "post.json"
    hook(workdir, "post", "PostToolUse", f"open({str(dump)!r}, 'w').write(json.dumps(payload))\n")
    services.harness.reload()
    sess = await services.create_session()
    await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    payload = json.loads(dump.read_text())
    assert payload["event"] == "PostToolUse" and payload["session_id"] == sess.id
    assert payload["tool"] == "read" and payload["arguments"] == {"path": "a.txt"}
    assert "hello" in payload["result"] and payload["is_error"] is False
    assert "agent" in payload


async def test_hook_env_and_cwd(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    dump = workdir / "env.json"
    hook(
        workdir,
        "envdump",
        "PreToolUse",
        f"open({str(dump)!r}, 'w').write(json.dumps({{'cwd': os.getcwd(), 'env': dict(os.environ)}}))\n",
    )
    services.harness.reload()
    sess = await services.create_session()
    await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    data = json.loads(dump.read_text())
    assert data["cwd"] == str(services.cwd)
    assert data["env"]["AUOREO_SESSION_ID"] == sess.id
    assert data["env"]["AUOREO_HOOK_EVENT"] == "PreToolUse"
    assert "AUOREO_HOME" not in data["env"]  # scrubbed env


# ------------------------------------------------------------- error policies
@pytest.mark.parametrize(
    ("script", "timeout_ms", "needle"),
    [
        ("time.sleep(3)\n", 200, "timed out"),
        ("print('not json')\n", 5000, "not JSON"),
        ("sys.exit(3)\n", 5000, "exit code 3"),
    ],
)
async def test_on_error_policies(
    services: Services, workdir: Path, script: str, timeout_ms: int, needle: str
) -> None:
    (workdir / "a.txt").write_text("A")
    hook(workdir, "flaky", "PreToolUse", script, timeout_ms=timeout_ms, on_error="warn")
    services.harness.reload()
    sess = await services.create_session()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert not err  # warn: logged, the call proceeds
    assert (await events(services, sess.id, "hook.ran"))[-1].data["decision"] == "error"
    hook(workdir, "flaky", "PreToolUse", script, timeout_ms=timeout_ms, on_error="block")
    services.harness.reload()
    text, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert err and needle in text and "on_error: block" in text


async def test_no_recursion_inside_a_hook_process(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workdir / "a.txt").write_text("A")
    hook(workdir, "deny_all", "PreToolUse", reply({"decision": "deny"}))
    services.harness.reload()
    monkeypatch.setenv("AUOREO_HOOK_EVENT", "PreToolUse")  # we are a process started by a hook
    sess = await services.create_session()
    _, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert not err and await events(services, sess.id, "hook.ran") == []


# ------------------------------------------------------------------ runner seams
async def test_user_prompt_submit_modify_and_deny(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    hook(
        workdir,
        "rewrite",
        "UserPromptSubmit",
        "if 'secret' in payload['text']:\n"
        "    print(json.dumps({'decision': 'deny', 'message': 'no secrets please'}))\n"
        "else:\n"
        "    print(json.dumps({'decision': 'modify', 'text': payload['text'].upper()}))\n",
    )
    services.harness.reload()
    sid, status = await run_turn(services, "hello there")
    assert status == "completed"
    hist = await services.coordinator.runner.load_history(sid)
    assert hist[0] == {"role": "user", "content": "HELLO THERE"}
    assert hist[-1]["content"] == "echo: HELLO THERE"
    sid, status = await run_turn(services, "my secret")
    assert status == "blocked"
    assert await services.coordinator.runner.load_history(sid) == []
    blocked = await events(services, sid, "run.blocked")
    assert blocked[0].data["error"] == {"code": "HOOK_DENIED", "message": "no secrets please"}


async def test_stop_hook_blocks_then_caps_at_three(services: Services, workdir: Path) -> None:
    hook(workdir, "keep_going", "Stop", reply({"block": True, "reason": "run the tests first"}))
    services.harness.reload()
    sid, status = await run_turn(services, "done?")
    assert status == "completed"
    hist = await services.coordinator.runner.load_history(sid)
    stops = [m for m in hist if m["role"] == "user" and str(m["content"]).startswith("<stop-hook>")]
    assert len(stops) == 3
    assert stops[0]["content"] == "<stop-hook>\nrun the tests first\n</stop-hook>"
    ran = await events(services, sid, "hook.ran")
    assert [e.data["decision"] for e in ran] == ["block"] * 3
    assert hist[-1]["role"] == "assistant"


async def test_stop_hook_block_once_continues(services: Services, workdir: Path) -> None:
    flag = workdir / "stopped.flag"
    hook(
        workdir,
        "once",
        "Stop",
        f"import pathlib\nf = pathlib.Path({str(flag)!r})\n"
        "if not f.exists():\n    f.write_text('x')\n"
        "    print(json.dumps({'block': True, 'reason': 'one more thing'}))\n",
    )
    services.harness.reload()
    sid, status = await run_turn(services, "hi")
    assert status == "completed"
    hist = await services.coordinator.runner.load_history(sid)
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert "one more thing" in hist[2]["content"]


async def test_no_hooks_on_api_error_turns(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    hook(workdir, "stopper", "Stop", reply({"block": True, "reason": "x"}))
    services.harness.reload()
    gateway.queue(Scripted(error=GatewayError("RATE_LIMIT", "slow down")))
    sid, status = await run_turn(services, "hi")
    assert status == "failed"
    assert await events(services, sid, "hook.ran") == []


async def test_session_start_once_and_session_end_on_archive(services: Services, workdir: Path) -> None:
    log = workdir / "lifecycle.log"
    script = f"open({str(log)!r}, 'a').write(payload['event'] + ' ' + payload['agent'] + '\\n')\n"
    hook(workdir, "start", "SessionStart", script, matcher="default")
    hook(workdir, "end", "SessionEnd", script)
    services.harness.reload()
    sess = await services.create_session()
    for text in ("one", "two"):
        await services.coordinator.submit(sess.id, text, interactive=False)
        await services.coordinator.wait(sess.id, 10)
    assert log.read_text().splitlines() == ["SessionStart default"]
    await set_archived(services, sess.id, True)
    for _ in range(100):
        if any(e.data["event"] == "SessionEnd" for e in await events(services, sess.id, "hook.ran")):
            break
        await asyncio.sleep(0.05)
    assert log.read_text().splitlines() == ["SessionStart default", "SessionEnd default"]


async def test_pre_and_post_compact(services: Services, workdir: Path) -> None:
    log = workdir / "compact.log"
    script = f"open({str(log)!r}, 'a').write(payload['event'] + ' ' + payload['trigger'] + '\\n')\n"
    hook(workdir, "pre", "PreCompact", script)
    hook(workdir, "post", "PostCompact", script)
    services.harness.reload()
    sess = await services.create_session()
    for i in range(15):
        await services.coordinator.submit(sess.id, f"turn {i}: {FILLER}", interactive=False)
        await services.coordinator.wait(sess.id, 10)
    await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    assert log.read_text().splitlines() == ["PreCompact manual", "PostCompact manual"]


async def test_disabled_hooks_never_run(services: Services, workdir: Path) -> None:
    (workdir / "a.txt").write_text("A")
    hook(workdir, "off", "PreToolUse", reply({"decision": "deny"}))
    p = workdir / ".auoreo" / "hooks" / "off.yaml"
    p.write_text(p.read_text() + "enabled: false\n")
    services.harness.reload()
    sess = await services.create_session()
    _, err = await call_tool(services, sess.id, "read", {"path": "a.txt"}, workdir)
    assert not err
