"""F3: Perfetto/Chrome Trace-Event writer driven by the observability hooks (OTel off)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import observability as obs
from auoreo.core import tracefile
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted


@pytest.fixture
def trace(home: Path, tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "trace.json"
    obs.reset()
    assert obs.setup(Settings(home=home, trace_file=path)) == "off"
    assert tracefile.active() is not None
    yield path
    tracefile.close()
    obs.reset()


def test_valid_json_and_metadata(trace: Path) -> None:
    with obs.span("agent.run", model="m", agent="default") as span:
        span.set("status", "completed")
        span.content("prompt", "SECRET PROMPT")
    obs.count("compaction_runs_total")
    tracefile.close()
    events = json.loads(trace.read_text())  # strict JSON once closed
    lines = trace.read_text().splitlines()
    assert lines[0] == "[" and lines[-1] == "]"
    names = {e["args"]["name"] for e in events if e["ph"] == "M" and e["name"] == "thread_name"}
    assert names == {"interaction", "llm", "tool"}
    (run,) = [e for e in events if e["ph"] == "X"]
    assert run["name"] == "agent.run" and run["tid"] == 1 and run["cat"] == "interaction"
    assert run["args"] == {"model": "m", "agent": "default", "status": "completed"}
    (inst,) = [e for e in events if e["ph"] == "i"]
    assert inst["name"] == "compaction" and inst["s"] == "t"
    assert "SECRET" not in trace.read_text()


def test_crashed_file_still_loads(trace: Path) -> None:
    with obs.span("tool.call", tool="read", decision="allow"):
        pass
    assert [e["name"] for e in tracefile.load(trace) if e["ph"] == "X"] == ["tool.call"]  # no closing ]


def test_error_and_approval_instant(trace: Path) -> None:
    async def boom() -> None:
        raise ValueError("bad")

    with pytest.raises(ValueError):
        asyncio.run(obs.traced(boom(), "tool.call", tool="bash", decision="ask"))
    tracefile.close()
    events = tracefile.load(trace)
    (call,) = [e for e in events if e["ph"] == "X"]
    assert call["args"] == {"tool": "bash", "decision": "ask", "error": "ValueError"}
    (appr,) = [e for e in events if e["ph"] == "i"]
    assert appr["name"] == "approval" and appr["args"] == {"tool": "bash", "decision": "approved"}


async def test_fake_gateway_turn_nesting(
    trace: Path, services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("TOP SECRET FILE BODY\n")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="the answer is 42"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "please read a.txt for me")
    await services.coordinator.wait(sess.id, 10)
    tracefile.close()
    raw = await asyncio.to_thread(trace.read_text)
    events = json.loads(raw)
    xs = [e for e in events if e["ph"] == "X"]
    by = {n: [e for e in xs if e["name"] == n] for n in ("agent.run", "llm.call", "tool.call")}
    (run,) = by["agent.run"]
    turn_llm = [e for e in by["llm.call"] if e["args"].get("purpose") != "title"]
    assert len(turn_llm) == 2 and len(by["tool.call"]) == 1
    end = run["ts"] + run["dur"]
    for e in turn_llm + by["tool.call"]:
        assert run["ts"] <= e["ts"] and e["ts"] + e["dur"] <= end + 1  # nested in the run
    first, second = sorted(turn_llm, key=lambda e: e["ts"])
    (tool,) = by["tool.call"]
    assert first["ts"] + first["dur"] <= tool["ts"] <= second["ts"]  # llm → tool → llm
    assert tool["tid"] == 3 and first["tid"] == 2
    assert tool["args"] == {"tool": "read", "decision": "allow"}
    assert set(first["args"]) <= {"model", "purpose", "tokens.prompt", "tokens.completion"}
    for needle in ("TOP SECRET", "please read", "the answer is 42", "a.txt"):
        assert needle not in raw

    result = CliRunner().invoke(app, ["trace", "summarize", str(trace)])
    assert result.exit_code == 0, result.output
    assert "agent.run" in result.output and "tool.call" in result.output and "p95 ms" in result.output
    rows = json.loads(CliRunner().invoke(app, ["trace", "summarize", str(trace), "--json"]).output)
    counts = {r["name"]: r["count"] for r in rows}
    assert counts["agent.run"] == 1 and counts["tool.call"] == 1 and counts["llm.call"] >= 2


def test_summarize_percentiles(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    events = [
        {"ph": "X", "name": "llm.call", "ts": i, "dur": d * 1000} for i, d in enumerate([1, 2, 3, 4, 100])
    ]
    path.write_text(json.dumps(events))
    (row,) = tracefile.summarize(path)
    assert row["count"] == 5 and row["total_ms"] == 110 and row["p50_ms"] == 3 and row["p95_ms"] == 100
    bad = tmp_path / "bad.json"
    bad.write_text("nope")
    assert CliRunner().invoke(app, ["trace", "summarize", str(bad)]).exit_code != 0


def test_off_writes_nothing(home: Path) -> None:
    obs.reset()
    obs.setup(Settings(home=home))
    assert tracefile.active() is None
    with obs.span("agent.run"):
        pass


async def test_app_shutdown_closes_file(trace: Path, services: Services, home: Path) -> None:
    from asgi_lifespan import LifespanManager

    from auoreo.api.app import create_app

    app = create_app(Settings(home=home, gateway="fake", trace_file=trace), services=services)
    async with LifespanManager(app):
        with obs.span("agent.run", model="m"):
            pass
        assert tracefile.active() is not None
    assert tracefile.active() is None  # closed by the lifespan, not atexit (uvicorn re-raises SIGTERM)
    text = await asyncio.to_thread(trace.read_text)
    assert text.rstrip().endswith("]") and any(e["name"] == "agent.run" for e in json.loads(text))
