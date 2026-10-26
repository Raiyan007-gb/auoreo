"""G4a tool engine: availability (check_fn / is_enabled / env_gate), output_schema, lossless
args, progress, collisions, per-path locks, capped spill, tool_describe / tool_call."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.discovery import default_roots
from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import Rule, ToolManifest
from auoreo.harness.orchestrator import build_spec
from auoreo.harness.registry import Harness, HarnessError, ToolCollisionError
from auoreo.tools.args import InvalidArgumentsError, snapshot_json_value
from auoreo.tools.availability import Availability
from auoreo.tools.context import ToolContext
from auoreo.tools.locks import PathLocks, patch_paths
from auoreo.tools.stages import prune
from tests.conftest import REPO, make_tool, write

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def _ctx(svc: Services, workdir: Path, sid: str = "s", **services: Any) -> ToolContext:
    spec = build_spec(svc.harness, "default", "default", "manual")
    engine = PermissionEngine(list(ALLOW))
    return ToolContext(
        session_id=sid,
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"services": svc, "engine": engine, "spec": spec, **services},
    )


async def _run(svc: Services, ctx: ToolContext, name: str, raw: str | dict[str, Any], cid: str = "c1") -> Any:
    args = raw if isinstance(raw, str) else json.dumps(raw)
    return await svc.coordinator.runner.executor.run(
        ToolCall(cid, name, args), tools=svc.harness.tools, ctx=ctx, engine=ctx.services["engine"]
    )


def _reload(svc: Services) -> None:
    svc.harness.reload()


# ------------------------------------------------------------------ 1. availability
async def test_check_fn_hides_tool_with_reason_and_is_cached(services: Services, workdir: Path) -> None:
    root = workdir / ".auoreo"
    d = make_tool(root, "probe_me", extra="check_fn: handler.py:available\n")
    write(
        d / "handler.py",
        "STATE = {'n': 0}\nasync def run(args, ctx):\n    return 'ran'\n"
        "def available():\n    STATE['n'] += 1\n    return 'binary xyz not found'\n",
    )
    _reload(services)
    tool = services.harness.tools["probe_me"]
    av = services.harness.availability
    assert av.reason(tool) == "binary xyz not found"
    names = [t.name for t in services.harness.select_tools(services.harness.agent("default").manifest)]
    assert "probe_me" not in names
    row = next(r for r in services.harness.describe()["tools"] if r["name"] == "probe_me")
    assert row["available"] is False and row["unavailable_reason"] == "binary xyz not found"
    out = await _run(services, _ctx(services, workdir), "probe_me", {})
    assert out.result.is_error and "unavailable: binary xyz not found" in out.result.content


def test_check_fn_ttl_and_verdicts(tmp_path: Path) -> None:
    d = tmp_path / "t"
    write(d / "chk.py", "CALLS = []\ndef ok():\n    CALLS.append(1)\n    return len(CALLS) > 1\n")
    m = ToolManifest(name="t", description="x", check_fn="chk.py:ok")
    tool = ToolDef(m, "bundled", d)
    av = Availability(ttl=30)
    now = [100.0]
    av.clock = lambda: now[0]
    assert av.reason(tool) == "check_fn: unavailable"  # first probe returns False
    now[0] += 10
    assert av.reason(tool) == "check_fn: unavailable"  # cached, probe not re-run
    now[0] += 25
    assert av.reason(tool) is None  # TTL expired → probe ran again → True
    bad = ToolDef(ToolManifest(name="b", description="x", check_fn="nope_mod_zz:fn"), "bundled", d)
    assert (av.reason(bad) or "").startswith("check_fn failed: ModuleNotFoundError")
    with pytest.raises(ValueError):
        ToolManifest(name="c", description="x", check_fn="../evil.py:fn")


def test_is_enabled_and_env_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secrets: set[str] = set()
    av = Availability(has_secret=lambda n: n in secrets)

    def mk(**kw: Any) -> ToolDef:
        return ToolDef(ToolManifest(name="g", description="x", **kw), "bundled", tmp_path)

    assert av.reason(mk(is_enabled=False)) == "disabled (is_enabled: false)"
    monkeypatch.delenv("G4A_A", raising=False)
    monkeypatch.delenv("G4A_B", raising=False)
    assert av.reason(mk(requires_env=["G4A_A"])) is None  # env_gate none = pass-through only
    all_gate = mk(requires_env=["G4A_A", "G4A_B"], env_gate="all")
    any_gate = mk(requires_env=["G4A_A", "G4A_B"], env_gate="any")
    assert av.reason(all_gate) == "missing env/secret: G4A_A, G4A_B"
    assert av.reason(any_gate) == "needs one of: G4A_A, G4A_B"
    monkeypatch.setenv("G4A_A", "1")
    secrets.add("G4A_B")
    assert av.reason(all_gate) is None and av.reason(any_gate) is None


async def test_output_schema_validates_structured_results(services: Services, workdir: Path) -> None:
    root = workdir / ".auoreo"
    schema = "output_schema: {type: object, properties: {n: {type: integer}}, required: [n]}\nemoji: '🧪'\n"
    make_tool(root, "good_out", body="return '{\"n\": 3}'", extra=schema)
    make_tool(root, "bad_out", body='return \'{"n": "three"}\'', extra=schema)
    make_tool(root, "text_out", body="return 'not json'", extra=schema)
    _reload(services)
    ctx = _ctx(services, workdir)
    good = await _run(services, ctx, "good_out", {})
    assert not good.result.is_error and good.result.meta["structured"] == {"n": 3}
    bad = await _run(services, ctx, "bad_out", {})
    assert bad.result.is_error and "returned invalid output at n" in bad.result.content
    text = await _run(services, ctx, "text_out", {})
    assert text.result.is_error and "not JSON" in text.result.content
    row = next(r for r in services.harness.describe()["tools"] if r["name"] == "good_out")
    assert row["emoji"] == "🧪" and row["available"] is True


# ------------------------------------------------------------------ 2. lossless args
def test_snapshot_json_value_is_lossless_and_detached() -> None:
    snap = snapshot_json_value("t", '{"a": [1, {"b": 2}], "s": "9007199254740993"}')
    snap.dispatched["a"][1]["b"] = 99
    assert snap.logged == {"a": [1, {"b": 2}], "s": "9007199254740993"}
    bad = {
        '{"a": 1, "a": 2}': "duplicate key 'a'",
        '{"n": 9007199254740993}': "exceeds 2^53-1",
        '{"x": NaN}': "NaN is not valid JSON",
        "[1, 2]": "must be a JSON object",
        "{oops": "not valid JSON",
    }
    for raw, needle in bad.items():
        with pytest.raises(InvalidArgumentsError) as info:
            snapshot_json_value("t", raw)
        assert needle in info.value.detail and info.value.tool == "t"


async def test_executor_returns_typed_invalid_arguments(services: Services, workdir: Path) -> None:
    root = workdir / ".auoreo"
    make_tool(
        root,
        "typed",
        body="args['n'] = -1\n    return 'ok'",
        extra="parameters: {type: object, properties: {n: {type: integer}}, required: [n]}\n",
    )
    _reload(services)
    ctx = _ctx(services, workdir)
    for raw, needle in (
        ('{"n": "5"}', "invalid arguments at n"),  # never coerced to 5
        ('{"n": 1, "n": 2}', "duplicate key"),
        ('{"n": 12345678901234567890}', "2^53-1"),
    ):
        out = await _run(services, ctx, "typed", raw)
        assert out.result.is_error and out.result.content.startswith("InvalidArgumentsError: typed:")
        assert needle in out.result.content
    ok = await _run(services, ctx, "typed", '{"n": 5}')
    assert ok.result.content == "ok"


# ------------------------------------------------------------------ 3. progress
async def test_progress_events_are_emitted_and_rate_limited(services: Services, workdir: Path) -> None:
    root = workdir / ".auoreo"
    body = (
        "sent = []\n    for i in range(5):\n"
        "        sent.append(await ctx.progress(f'step {i}', percent=i * 10))\n"
        "    sent.append(await ctx.on_progress('done', percent=100, phase='finishing'))\n"
        "    return json.dumps(sent)"
    )
    d = make_tool(root, "slowpoke", body=body)
    write(d / "handler.py", "import json\n" + (d / "handler.py").read_text())
    _reload(services)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def emit(kind: str, data: dict[str, Any]) -> None:
        seen.append((kind, data))

    ctx = _ctx(services, workdir)
    ctx.emit = emit
    out = await _run(services, ctx, "slowpoke", {}, cid="call_p")
    assert json.loads(out.result.content) == [True, False, False, False, False, True]
    assert [k for k, _ in seen] == ["tool.progress", "tool.progress"]
    first, last = seen[0][1], seen[1][1]
    assert first == {
        "call_id": "call_p",
        "tool": "slowpoke",
        "phase": "running",
        "message": "step 0",
        "percent": 0.0,
        "cancellable": False,
    }
    assert last["percent"] == 100.0 and last["phase"] == "finishing"
    assert await ctx.progress("outside any call") is False


# ------------------------------------------------------------------ 4. collisions
def _overlay_tool(cwd: Path, name: str = "read") -> None:
    make_tool(cwd / ".auoreo", name)


def test_error_on_collision_is_fatal_for_overlays(home: Path, workdir: Path) -> None:
    _overlay_tool(workdir)
    roots = default_roots(home, workdir, REPO, [])
    lenient = Harness(roots)
    assert any("shadows a bundled tool" in str(e) for e in lenient.errors)
    assert lenient.tools["read"].source == "bundled"
    with pytest.raises(HarnessError, match="shadows a bundled tool"):
        Harness(roots, error_on_collision=True)


def test_dynamic_collision_warns_or_raises(home: Path, workdir: Path) -> None:
    roots = default_roots(home, workdir, REPO, [])
    clash = ToolDef(ToolManifest(name="read", description="mcp read"), "mcp:x", None)
    lenient = Harness(roots)
    lenient.register_dynamic(clash)
    assert lenient.tools["read"].source == "bundled"  # builtins win
    strict = Harness(roots, error_on_collision=True)
    with pytest.raises(ToolCollisionError):
        strict.register_dynamic(clash)
    assert any("collides with the bundled one" in str(e) for e in strict.errors)
    strict.register_dynamic(ToolDef(ToolManifest(name="mcp__x__y", description="y"), "mcp:x", None))
    strict.register_dynamic(ToolDef(ToolManifest(name="mcp__x__y", description="y"), "mcp:x", None))
    with pytest.raises(ToolCollisionError):
        strict.register_dynamic(ToolDef(ToolManifest(name="mcp__x__y", description="y"), "mcp:z", None))


def test_settings_carry_tool_engine_block(home: Path) -> None:
    st = Settings(home=home, tool_engine={"error_on_collision": True, "spill_max_bytes": 4096})
    assert st.tool_engine.error_on_collision and st.tool_engine.spill_max_bytes == 4096
    assert Settings(home=home).tool_engine.spill_max_bytes == 64 * 1024 * 1024


# ------------------------------------------------------------------ 5. per-path locks
def test_patch_paths_parses_codex_and_unified_headers() -> None:
    codex = (
        "*** Begin Patch\n*** Update File: b.py\n*** Move to: c.py\n@@\n-x\n+y\n*** Add File: a.py\n+n\n"
        "*** Delete File: d.py\n*** End Patch\n"
    )
    assert patch_paths(codex) == ["b.py", "c.py", "a.py", "d.py"]
    unified = "--- a/x.txt\n+++ b/x.txt\n@@ -1 +1 @@\n-a\n+b\n--- /dev/null\n+++ b/new.txt\n"
    assert patch_paths(unified) == ["x.txt", "x.txt", "new.txt"]


async def test_path_locks_sorted_no_deadlock() -> None:
    locks = PathLocks()
    order: list[str] = []

    async def job(name: str, paths: list[str]) -> None:
        async with locks.hold(paths):
            order.append(f"{name}+")
            await asyncio.sleep(0.02)
            order.append(f"{name}-")

    await asyncio.wait_for(asyncio.gather(job("A", ["/a", "/b"]), job("B", ["/b", "/a"])), 2)
    assert order in (["A+", "A-", "B+", "B-"], ["B+", "B-", "A+", "A-"])
    assert not locks.locked("/a")


async def test_writers_on_one_path_serialize_and_patch_locks_all_files(
    services: Services, workdir: Path
) -> None:
    root = workdir / ".auoreo"
    params = "parameters: {type: object, properties: {path: {type: string}}}\npattern_arg: path\n"
    body = (
        "import asyncio\nLOG = []\nasync def run(args, ctx):\n    LOG.append('in')\n"
        "    await asyncio.sleep(0.05)\n    LOG.append('out')\n    return ''.join(LOG)\n"
    )
    d = make_tool(root, "slow_write", extra=params)
    write(d / "handler.py", body)
    p = make_tool(
        root, "fake_patch", extra="parameters: {type: object, properties: {patch: {type: string}}}\n"
    )
    write(
        p / "handler.py",
        "async def run(args, ctx):\n    ex = ctx.services['executor']\n"
        "    return str(sorted(k for k in ex.path_locks._locks if ex.path_locks.locked(k)))\n",
    )
    _reload(services)
    ctx = _ctx(services, workdir)
    a, b = await asyncio.gather(
        _run(services, ctx, "slow_write", {"path": "f.txt"}, "w1"),
        _run(services, ctx, "slow_write", {"path": "f.txt"}, "w2"),
    )
    assert sorted([a.result.content, b.result.content]) == ["inout", "inoutinout"]  # no interleave
    patch = "*** Begin Patch\n*** Update File: z.py\n*** Add File: m.py\n+x\n*** End Patch\n"
    held = await _run(services, ctx, "fake_patch", {"patch": patch})
    assert held.result.content == str(sorted([str(workdir / "m.py"), str(workdir / "z.py")]))


# ------------------------------------------------------------------ 7. spill cap
def test_spill_is_capped_and_noted(tmp_path: Path) -> None:
    text = "x" * 50_000
    out, meta = prune(text, 2_000, tmp_path, "c9", spill_cap=10_000)
    spilled = (tmp_path / "c9.txt").read_bytes()
    assert len(spilled) <= 10_000 and b"spill truncated at 10000 of 50000 bytes" in spilled
    assert meta["spill_truncated"] is True and "first 10000 of 50000 bytes kept" in out
    small, meta2 = prune("y" * 3_000, 2_000, tmp_path, "c10")
    assert "full output:" in small and "spill_truncated" not in meta2


async def test_executor_uses_configured_spill_cap(services: Services, workdir: Path) -> None:
    make_tool(workdir / ".auoreo", "big", body="return 'z' * 200_000")
    _reload(services)
    services.settings.tool_engine.spill_max_bytes = 50_000
    out = await _run(services, _ctx(services, workdir), "big", {}, "c_big")
    assert out.result.meta["spill_truncated"] is True
    assert len(await asyncio.to_thread(Path(out.result.meta["output_path"]).read_bytes)) <= 50_000


# ------------------------------------------------------------------ 6. meta-tools
async def test_tool_describe_and_tool_call_for_deferred_tools(services: Services, workdir: Path) -> None:
    root = workdir / ".auoreo"
    extra = (
        "exposure: deferred\nparameters: {type: object, properties: {n: {type: integer}}, required: [n]}\n"
    )
    make_tool(root, "hidden_add", body="return str(args['n'] + 1)", extra=extra)
    make_tool(root, "hidden_denied", body="return 'nope'", extra="exposure: deferred\n")
    _reload(services)
    ctx = _ctx(services, workdir)
    desc = await _run(services, ctx, "tool_describe", {"name": "hidden_add"})
    assert not desc.result.is_error and "deferred: run it with tool_call" in desc.result.content
    assert '"n"' in desc.result.content
    called = await _run(services, ctx, "tool_call", {"name": "hidden_add", "arguments": {"n": 41}})
    assert called.result.content == "42" and called.result.meta["inner_tool"] == "hidden_add"
    invalid = await _run(services, ctx, "tool_call", {"name": "hidden_add", "arguments": {"n": "x"}})
    assert invalid.result.is_error and "InvalidArgumentsError: hidden_add" in invalid.result.content
    ctx.services["engine"] = PermissionEngine(
        [*ALLOW, Rule(permission="hidden_denied", pattern="*", action="deny")]
    )
    denied = await _run(services, ctx, "tool_call", {"name": "hidden_denied"})
    assert denied.result.is_error and "permission denied" in denied.result.content
    direct = await _run(services, ctx, "tool_call", {"name": "read", "arguments": {}})
    assert direct.result.is_error and "direct tool" in direct.result.content
    meta = await _run(services, ctx, "tool_call", {"name": "tool_call"})
    assert meta.result.is_error and "cannot be called" in meta.result.content


def test_meta_tools_are_core_and_direct(services: Services) -> None:
    for name in ("tool_describe", "tool_call"):
        m = services.harness.tools[name].manifest
        assert m.exposure == "direct" and m.toolset == "core"
