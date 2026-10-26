"""I6 tool manifest `progressSchema` (validates `ctx.progress(data=…)`) and `activityHint`
(UI label on `tool.started` + harness listing) (spec §25.7 L977); creator `vmTimeoutMs`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule, ToolManifest
from auoreo.plugins.install import RT_FILE
from auoreo.plugins.protocol import RpcError, RpcPeer
from auoreo.tools.activity import render_activity
from auoreo.tools.context import ToolContext
from tests.conftest import make_tool, write

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def test_manifest_accepts_both_spellings_and_rejects_bad_schemas() -> None:
    base = {"name": "t", "description": "d"}
    m = ToolManifest.model_validate(
        {**base, "progressSchema": {"type": "object"}, "activityHint": "Doing {x}"}
    )
    assert m.progress_schema == {"type": "object"} and m.activity_hint == "Doing {x}"
    m2 = ToolManifest.model_validate({**base, "progress_schema": {"type": "object"}, "activity_hint": "y"})
    assert m2.activity_hint == "y"
    with pytest.raises(ValidationError, match="progressSchema is not a valid JSON Schema"):
        ToolManifest.model_validate({**base, "progressSchema": {"type": 12}})
    with pytest.raises(ValidationError):
        ToolManifest.model_validate({**base, "activityHint": "x" * 121})


def test_render_activity() -> None:
    assert render_activity("Reading {path}", '{"path": "src/a.py"}') == "Reading src/a.py"
    assert render_activity("Reading {path}", "{}") == "Reading {path}"  # missing stays literal
    assert render_activity("Got {n} {flags}", {"n": 3, "flags": ["a"]}) == 'Got 3 ["a"]'
    long = render_activity("Run {command}", {"command": "x" * 200})
    assert len(long) <= 80 and long.endswith("…")
    assert render_activity("", "{}") == "" and render_activity("Hi {x}", "not json") == "Hi {x}"


async def test_progress_data_is_validated_against_progress_schema(services: Services, workdir: Path) -> None:
    schema = "progressSchema: {type: object, properties: {done: {type: integer}}, required: [done]}\n"
    body = (
        "a = await ctx.progress('one', data={'done': 'nope'})\n"
        "    b = await ctx.progress('two', percent=100, data={'done': 3})\n"
        "    return json.dumps([a, b])"
    )
    d = make_tool(workdir / ".auoreo", "counter", body=body, extra=schema)
    write(d / "handler.py", "import json\n" + (d / "handler.py").read_text())
    services.harness.reload()
    seen: list[tuple[str, dict[str, Any]]] = []

    async def emit(kind: str, data: dict[str, Any]) -> None:
        seen.append((kind, data))

    ctx = ToolContext(session_id="s", cwd=workdir, home=services.settings.home, harness=services.harness)
    ctx.emit = emit
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "counter", "{}"),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(list(ALLOW)),
    )
    assert json.loads(out.result.content) == [False, True]
    assert [d["data"] for k, d in seen if k == "tool.progress"] == [{"done": 3}]


async def test_activity_hint_on_tool_started_and_in_the_listing(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    make_tool(workdir / ".auoreo", "greeter", extra='activityHint: "Greeting {who}"\n')
    services.harness.reload()
    row = next(r for r in services.harness.describe()["tools"] if r["name"] == "greeter")
    assert row["activity_hint"] == "Greeting {who}"
    gateway.queue(Scripted(tool_calls=[("greeter", {"who": "Ada"})]), Scripted(text="done"))
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    rt.engine = PermissionEngine(list(ALLOW))
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    await services.coordinator.submit(sess.id, "greet", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    started = []
    while not sub.queue.empty():
        ev = sub.queue.get_nowait()
        if ev.type == "tool.started":
            started.append(ev.data)
    assert started and started[0]["activity"] == "Greeting Ada"


# ------------------------------------------------------------------ creator vmTimeoutMs
SYNC_LOOP = "def run(args, ctx):\n    while True:\n        pass\n"
SYNC_FAST = "def run(args, ctx):\n    return 'quick'\n"
ASYNC_SLOW = (
    "import asyncio\n\nasync def run(args, ctx):\n    await asyncio.sleep(0.6)\n    return 'escaped'\n"
)


async def _sandbox(tmp_path: Path, code: str, vm_ms: int) -> RpcPeer:
    path = tmp_path / "tool.py"
    path.write_text(code, encoding="utf-8")

    async def on_request(method: str, params: dict[str, Any]) -> Any:
        raise RpcError("no host calls", code=-32001)

    async def on_notify(method: str, params: dict[str, Any]) -> None:
        return None

    argv = [sys.executable, "-I", str(RT_FILE), "--ephemeral", str(path), "--name", "t"]
    peer = RpcPeer("t", argv, env={}, cwd=tmp_path, on_request=on_request, on_notify=on_notify)
    await peer.start()
    await peer.request("initialize", {"config": {"vm_timeout_ms": vm_ms}, "consumes_granted": []}, timeout=20)
    return peer


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX interval timer")
async def test_vm_timeout_bounds_sync_bodies_only(tmp_path: Path) -> None:
    peer = await _sandbox(tmp_path, SYNC_LOOP, 300)
    try:
        with pytest.raises(RpcError, match="vmTimeoutMs"):
            await peer.request("tool.call", {"name": "t", "args": {}, "session_id": "s"}, timeout=10)
    finally:
        await peer.close()
    peer = await _sandbox(tmp_path, SYNC_FAST, 300)
    try:
        res = await peer.request("tool.call", {"name": "t", "args": {}, "session_id": "s"}, timeout=10)
        assert res["content"] == "quick"
    finally:
        await peer.close()
    peer = await _sandbox(tmp_path, ASYNC_SLOW, 100)  # async body escapes the 100 ms bound
    try:
        res = await peer.request("tool.call", {"name": "t", "args": {}, "session_id": "s"}, timeout=10)
        assert res["content"] == "escaped"
    finally:
        await peer.close()


def test_vm_timeout_setting_default() -> None:
    from auoreo.core.settings import Settings

    assert Settings(gateway="fake").i6.creator.vm_timeout_ms == 5000
