"""H5 tools: forecast (+ ops sample store), run_code, review, video_analyze, workflow, and the
computer-use lock / delivery refusal codes."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools import cu_lock, opsmetrics
from auoreo.tools.context import ToolContext, ToolResult

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(
        home=home, gateway="fake", approval_timeout_s=2, backends={"sandbox": "none", "desktop": "fake"}
    )


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


def ctx_for(svc: Services, workdir: Path, rules: list[Rule] | None = None, sid: str = "s1") -> ToolContext:
    return ToolContext(
        session_id=sid,
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"services": svc, "engine": PermissionEngine(list(ALLOW if rules is None else rules))},
    )


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> ToolResult:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result


# ------------------------------------------------------------------ forecast
def test_trend_math() -> None:
    pts = [(1000.0 + 3600 * i, 50.0 + 2.0 * i) for i in range(10)]  # +2 per hour, exact
    fit = opsmetrics.linear_fit(pts)
    assert fit is not None and abs(fit.slope * 3600 - 2.0) < 1e-9 and fit.r2 == pytest.approx(1.0)
    now = pts[-1][0]
    assert opsmetrics.time_to_threshold(fit, fit.at(now), 90.0) == pytest.approx(11 * 3600)
    assert opsmetrics.time_to_threshold(fit, 95.0, 90.0) == 0.0
    falling = opsmetrics.linear_fit([(0.0, 10.0), (60.0, 9.0), (120.0, 8.0)])
    assert falling is not None and opsmetrics.time_to_threshold(falling, 8.0, 90.0) is None
    assert opsmetrics.time_to_threshold(falling, 8.0, 5.0, "below") == pytest.approx(180.0)
    assert opsmetrics.time_to_threshold(fit, 50.0, 5.0, "below") is None
    assert (
        opsmetrics.linear_fit([(5.0, 1.0), (5.0, 2.0)]) is None
        and opsmetrics.linear_fit([(1.0, 1.0)]) is None
    )
    assert opsmetrics.ewma([10.0, 20.0], 0.5) == 15.0 and opsmetrics.ewma([], 0.3) is None
    assert opsmetrics.human_duration(7200) == "2.0 h"


def test_metric_store_prunes_and_lists(tmp_path: Path) -> None:
    for i in range(15):
        opsmetrics.record(tmp_path, "mem.percent", float(i), ts=100.0 + i, max_samples=10)
    pts = opsmetrics.series(tmp_path, "mem.percent")
    assert len(pts) == 10 and pts[0] == (105.0, 5.0)
    assert opsmetrics.metrics(tmp_path) == [("mem.percent", 10, 114.0, 14.0)]
    with pytest.raises(ValueError):
        opsmetrics.record(tmp_path, "Bad Name", 1.0)
    assert opsmetrics.series(tmp_path / "none", "x") == []


async def test_forecast_tool(services: Services, workdir: Path) -> None:
    home = services.settings.home
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "forecast", {})
    assert not res.is_error and "no metrics sampled yet" in res.content
    now = time.time()
    for i in range(12):  # disk +1 %/h for 12 h, ending at 80 %
        opsmetrics.record(home, "disk.percent:/data", 69.0 + i, ts=now - 3600 * (11 - i))
    res = await call(
        services, ctx, "forecast", {"metric": "disk.percent:/data", "threshold": 90, "horizon_h": 5}
    )
    assert not res.is_error, res.content
    assert "+1.000/h" in res.content and "projected in 5.0 h: 85.00" in res.content
    assert res.meta["eta_s"] == pytest.approx(10 * 3600, rel=0.05)
    res = await call(services, ctx, "forecast", {"metric": "cpu.percent"})
    assert res.is_error and "need at least 3" in res.content and "disk.percent:/data" in res.content
    res = await call(services, ctx, "forecast", {"list": True})
    assert "| disk.percent:/data | 12 |" in res.content


async def test_ops_tools_feed_the_store(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    for name in ("ops_cpu", "ops_mem", "ops_disk"):
        res = await call(services, ctx, name, {"interval_s": 0.1} if name == "ops_cpu" else {})
        assert not res.is_error, res.content
    names = {m for m, *_ in opsmetrics.metrics(services.settings.home)}
    assert {"cpu.percent", "mem.percent", "swap.percent"} <= names
    assert any(n.startswith("disk.percent:/") for n in names)
    res = await call(services, ctx, "forecast", {"metric": "mem.percent", "sample_now": True})
    assert res.is_error and "need at least 3" in res.content  # 2 samples so far
    services.settings.h5.forecast.record_from_ops_tools = False
    before = opsmetrics.series(services.settings.home, "cpu.percent")
    await call(services, ctx, "ops_cpu", {"interval_s": 0.1})
    assert opsmetrics.series(services.settings.home, "cpu.percent") == before


# ------------------------------------------------------------------ run_code
async def test_run_code_languages_caps_and_timeout(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "run_code", {"language": "python", "code": "print(6 * 7)"})
    assert not res.is_error and res.content.startswith("42") and "[exit code 0]" in res.content
    res = await call(services, ctx, "run_code", {"language": "bash", "code": "echo hi; exit 3"})
    assert res.is_error and "hi" in res.content and "[exit code 3]" in res.content
    services.settings.h5.run_code.max_output_chars = 1000
    res = await call(services, ctx, "run_code", {"language": "python", "code": "print('x' * 50000)"})
    assert "chars omitted" in res.content and len(res.content) < 2000
    res = await call(
        services,
        ctx,
        "run_code",
        {"language": "python", "code": "import time; time.sleep(5)", "timeout_s": 1},
    )
    assert res.is_error and "timed out after 1s" in res.content
    if shutil.which("node"):
        res = await call(services, ctx, "run_code", {"language": "javascript", "code": "console.log(1 + 1)"})
        assert not res.is_error and res.content.startswith("2")


async def test_run_code_needs_approval_and_interpreter(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = services.harness.tools["run_code"].manifest
    assert (m.default_action, m.sandbox_mode, m.net, m.exposure) == ("ask", "read-only", "none", "deferred")
    res = await call(
        services, ctx_for(services, workdir, rules=[]), "run_code", {"language": "python", "code": "1"}
    )
    assert res.is_error  # non-interactive + ask → denied
    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda n, *a, **k: None if n in ("node", "nodejs") else real(n, *a, **k)
    )
    res = await call(
        services, ctx_for(services, workdir), "run_code", {"language": "javascript", "code": "1"}
    )
    assert res.is_error and "no javascript interpreter" in res.content


# ------------------------------------------------------------------ review
def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t"}
    env["GIT_COMMITTER_EMAIL"] = "t@x"
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_review_tool_static(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "review", {})
    assert res.is_error and "not a git repository" in res.content
    _git(workdir, "init", "-q")
    (workdir / "a.py").write_text("x = 1\n")
    _git(workdir, "add", ".")
    _git(workdir, "commit", "-qm", "init")
    res = await call(services, ctx, "review", {})
    assert not res.is_error and "clean" in res.content
    assert res.meta["structured"] == {"blockingErrors": [], "preventContinuation": False, "files": []}
    (workdir / "a.py").write_text("x = 2   \n")
    (workdir / "b.py").write_text("<<<<<<< HEAD\ny = 1\n=======\ny = 2\n>>>>>>> other\n")
    res = await call(services, ctx, "review", {})
    s = res.meta["structured"]
    assert s["preventContinuation"] and set(s["files"]) == {"a.py", "b.py"}
    assert any("trailing whitespace" in b for b in s["blockingErrors"])
    assert any("b.py: unresolved merge conflict" in b for b in s["blockingErrors"])
    assert "```diff" in res.content and "-x = 1" in res.content


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_review_tool_agent_mode(services: Services, workdir: Path) -> None:
    _git(workdir, "init", "-q")
    (workdir / "a.py").write_text("x = 1\n")
    parent = await services.create_session(title="p")
    res = await call(services, ctx_for(services, workdir, sid=parent.id), "review", {"mode": "agent"})
    assert "reviewer agent:" in res.content, res.content
    assert "a.py" in res.content and res.meta["structured"]["files"] == ["a.py"]


# ------------------------------------------------------------------ video_analyze
async def test_video_analyze_without_ffmpeg(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workdir / "clip.mp4").write_bytes(b"\x00" * 100)
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "ffmpeg" else real(n, *a, **k))
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "video_analyze", {"path": "clip.mp4", "question": "what?"})
    assert res.is_error and "needs ffmpeg" in res.content and "apt install ffmpeg" in res.content
    res = await call(services, ctx, "video_analyze", {"path": "clip.txt", "question": "what?"})
    assert res.is_error and "unsupported video type" in res.content
    services.settings.h5.video.max_bytes = 10
    res = await call(services, ctx, "video_analyze", {"path": "clip.mp4", "question": "what?"})
    assert res.is_error and "the limit is" in res.content


def _make_clip(clip: Path) -> None:
    src = "testsrc=duration=2:size=64x48:rate=10"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", src, str(clip)], check=True)


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["sleep", "5"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_analyze_samples_frames(services: Services, workdir: Path, gateway: FakeGateway) -> None:
    clip = workdir / "clip.mp4"
    _make_clip(clip)
    res = await call(
        services,
        ctx_for(services, workdir),
        "video_analyze",
        {"path": "clip.mp4", "question": "Q?", "frames": 3},
    )
    assert not res.is_error, res.content
    assert res.content.startswith("vision: Q?") and "[+3 images]" in res.content
    assert res.meta["frames"] == 3 and res.meta["duration_s"] == pytest.approx(2.0, abs=0.2)
    req = [r for r in gateway.requests if r.metadata.get("purpose") == "vision"][-1]
    texts = [p["text"] for p in req.messages[-1]["content"] if p["type"] == "text"]
    assert texts[1].startswith("[frame at 0.3s of 2.0s]")


# ------------------------------------------------------------------ workflow
async def test_workflow_tool_runs_chain(services: Services, workdir: Path) -> None:
    parent = await services.create_session(title="parent")
    ctx = ctx_for(services, workdir, sid=parent.id)
    res = await call(services, ctx, "workflow", {"name": "chat", "input": "hello chain"})
    assert not res.is_error, res.content
    assert "hello chain" in res.content
    child = await services.get_session(res.meta["session_id"])
    assert child is not None and child.parent_id == parent.id and child.workflow == "chat"
    res = await call(services, ctx, "workflow", {"name": "nope", "input": "x"})
    assert res.is_error and "unknown workflow 'nope'" in res.content
    bg = await call(services, ctx, "workflow", {"name": "chat", "input": "later", "background": True})
    assert not bg.is_error and bg.meta["status"] == "running"
    await services.coordinator.wait(bg.meta["session_id"], 10)


async def test_workflow_tool_is_gated_and_depth_capped(services: Services, workdir: Path) -> None:
    assert services.harness.tools["workflow"].manifest.default_action == "ask"
    res = await call(
        services, ctx_for(services, workdir, rules=[]), "workflow", {"name": "chat", "input": "x"}
    )
    assert res.is_error
    a = await services.create_session(title="a")
    b = await services.create_session(title="b", parent_id=a.id)
    c = await services.create_session(title="c", parent_id=b.id)
    res = await call(
        services, ctx_for(services, workdir, sid=c.id), "workflow", {"name": "chat", "input": "x"}
    )
    assert res.is_error and "nesting limit" in res.content


# ------------------------------------------------------------------ computer use
def test_cu_lock_fresh_reentrant_blocked_and_stale(tmp_path: Path) -> None:
    assert cu_lock.acquire(tmp_path, "s1") == "fresh"
    assert cu_lock.acquire(tmp_path, "s1") == "reentrant"
    with pytest.raises(cu_lock.LockBlocked):
        cu_lock.acquire(tmp_path, "s2")  # same process, other session
    cu_lock.release(tmp_path, "s1")
    assert (tmp_path / cu_lock.LOCK_NAME).exists()  # still held once
    cu_lock.release(tmp_path, "s1")
    assert not (tmp_path / cu_lock.LOCK_NAME).exists()
    dead = subprocess.Popen(["true"])
    dead.wait()
    (tmp_path / cu_lock.LOCK_NAME).write_text(json.dumps({"pid": dead.pid, "session_id": "old"}))
    with cu_lock.held(tmp_path, "s3") as state:
        assert state == "recovered"
        assert json.loads((tmp_path / cu_lock.LOCK_NAME).read_text())["pid"] == os.getpid()
    live = subprocess.Popen(["sleep", "5"])
    try:
        (tmp_path / cu_lock.LOCK_NAME).write_text(json.dumps({"pid": live.pid, "session_id": "other"}))
        with pytest.raises(cu_lock.LockBlocked, match=str(live.pid)):
            cu_lock.acquire(tmp_path, "s3")
    finally:
        live.kill()
        live.wait()


async def test_computer_refusal_codes(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "computer", {"op": "click", "target": "5,5", "bring_to_front": True})
    assert res.is_error and "bring_to_front_requires_foreground" in res.content
    handler = sys.modules["auoreo_tool_computer"]  # loaded by the call above
    assert handler.delivery_refusal({"delivery_mode": "sideways"}).startswith("[bad_delivery_mode]")
    desk = services.backend("desktop")
    type(desk).supports_foreground = False
    try:
        res = await call(
            services, ctx, "computer", {"op": "click", "target": "5,5", "delivery_mode": "foreground"}
        )
        assert res.is_error and "foreground_unsupported" in res.content
    finally:
        type(desk).supports_foreground = True


async def test_computer_holds_lock_and_refuses_when_blocked(services: Services, workdir: Path) -> None:
    home = services.settings.home
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "computer", {"op": "click", "target": "5,5"})
    assert not res.is_error, res.content
    assert not (home / cu_lock.LOCK_NAME).exists()  # released after the action
    live = _sleeper()
    try:
        (home / cu_lock.LOCK_NAME).write_text(json.dumps({"pid": live.pid, "session_id": "x"}))
        res = await call(services, ctx, "computer", {"op": "click", "target": "5,5"})
        assert res.is_error and "computer_use_locked" in res.content
        services.settings.h5.computer_use.lock = False
        res = await call(services, ctx, "computer", {"op": "click", "target": "5,5"})
        assert not res.is_error
    finally:
        live.kill()
        live.wait()
    await asyncio.sleep(0)
