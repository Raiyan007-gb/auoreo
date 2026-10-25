"""OpenTelemetry wiring (spec §20.1 / §26.19): no-op when off, spans + allowlisted metrics."""

from __future__ import annotations

import builtins
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk", reason="otel extra not installed")

from auoreo.core import observability as obs  # noqa: E402
from auoreo.core.services import Services  # noqa: E402
from auoreo.core.settings import Settings  # noqa: E402
from auoreo.gateway.fake import FakeGateway, Scripted  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    obs.reset()
    yield
    obs.reset()


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch, home: Path) -> Iterator[None]:
    monkeypatch.setenv("AUOREO_OTEL_TEST", "1")
    assert obs.setup(Settings(home=home, otel_mode="local")) == "local"
    yield


def _points() -> list[tuple[str, dict[str, Any], float]]:
    data = obs.metric_reader().get_metrics_data()
    out = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for p in m.data.data_points:
                    out.append((m.name, dict(p.attributes or {}), p.value))
    return out


def test_off_is_a_noop(home: Path) -> None:
    assert obs.setup(Settings(home=home)) == "off"
    assert not obs.enabled() and obs.memory_exporter() is None and obs.metric_reader() is None
    with obs.span("agent.run", model="m") as span:
        span.set("status", "completed")
        span.content("body", "secret prompt")
    obs.count("events_published_total")
    obs.record_usage("m", 1, 2, 0.1)
    assert obs.status(Settings(home=home)) == ("ok", "off (set AUOREO_OTEL_MODE=local|otlp to trace)")


def test_setup_is_idempotent(local: None, home: Path) -> None:
    exporter = obs.memory_exporter()
    assert obs.setup(Settings(home=home, otel_mode="local")) == "local"
    assert obs.memory_exporter() is exporter
    assert obs.status(Settings(home=home, otel_mode="local"))[0] == "ok"


async def test_fake_turn_emits_nested_spans_and_bounded_metrics(
    local: None, services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="done"))
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "read a")
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed"

    spans = obs.memory_exporter().get_finished_spans()
    by_name: dict[str, list[Any]] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)
    (run,) = by_name["agent.run"]
    llm = by_name["llm.call"]
    (tool,) = by_name["tool.call"]
    assert len(llm) == 2
    assert run.attributes["status"] == "completed" and run.attributes["agent"]
    assert all(c.parent is not None and c.parent.span_id == run.context.span_id for c in [*llm, tool])
    assert {c.context.trace_id for c in [*llm, tool]} == {run.context.trace_id}
    assert llm[0].attributes["purpose"] == "turn" and llm[0].attributes["tokens.prompt"] > 0
    assert llm[0].end_time <= tool.start_time  # agent.run → llm.call → tool.call
    assert tool.attributes["tool"] == "read" and tool.attributes["decision"] in {"allow", "ask"}
    assert run.resource.attributes["service.name"] == "auoreo"
    for s in spans:  # bodies stay off by default
        assert "llm.completion" not in (s.attributes or {})
        assert sess.id not in {str(v) for v in (s.attributes or {}).values()}

    points = _points()
    names = {n for n, _, _ in points}
    assert names <= set(obs.METRICS)
    assert {"events_published_total", "run_completed_total", "tokens_total", "audit_writes_total"} <= names
    for name, attrs, _ in points:
        assert "session_id" not in attrs and set(attrs) <= obs.METRICS[name][1]
        assert sess.id not in attrs.values()
    runs = {a["status"]: v for n, a, v in points if n == "run_completed_total"}
    assert runs == {"completed": 1}
    types = {a["type"] for n, a, _ in points if n == "tokens_total"}
    assert types == {"prompt", "completion"}


def test_unknown_metrics_and_labels_are_dropped(local: None) -> None:
    obs.count("tokens_total", 5, model="m", type="prompt", session_id="ses_1", text="hello")
    obs.count("not_allowlisted_total", 1)
    obs.count("active_sse_connections", 1)
    obs.count("active_sse_connections", -1)
    points = _points()
    assert {n for n, _, _ in points} == {"tokens_total", "active_sse_connections"}
    assert [a for n, a, _ in points if n == "tokens_total"] == [{"model": "m", "type": "prompt"}]
    assert [v for n, _, v in points if n == "active_sse_connections"] == [0]


def test_capture_content_is_opt_in(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("AUOREO_OTEL_TEST", "1")
    obs.setup(Settings(home=home, otel_mode="local", otel_capture_content=True))
    with obs.span("x") as span:
        span.content("body", "hello")
    (s,) = obs.memory_exporter().get_finished_spans()
    assert s.attributes["body"] == "hello"


def test_span_records_errors(local: None) -> None:
    with pytest.raises(ValueError), obs.span("boom"):
        raise ValueError("bad")
    (s,) = obs.memory_exporter().get_finished_spans()
    assert s.status.status_code.name == "ERROR"


def test_missing_packages_degrade_to_off(
    monkeypatch: pytest.MonkeyPatch, home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level(logging.WARNING, logger="auoreo.core.observability"):
        assert obs.setup(Settings(home=home, otel_mode="otlp")) == "off"
        obs.reset()
        assert obs.setup(Settings(home=home, otel_mode="local")) == "off"
    assert caplog.text.count("OpenTelemetry is unavailable") == 1  # warned once
    assert not obs.enabled()
    with obs.span("agent.run") as span:
        span.set("status", "completed")


def test_otlp_status_and_setup(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    st = Settings(home=home, otel_mode="otlp")
    assert obs.status(st)[0] == "warn"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    assert obs.status(st) == ("ok", "otlp → http://127.0.0.1:9")
    assert obs.setup(st) == "otlp" and obs.enabled()


async def test_instrument_app_adds_server_spans(local: None, settings: Settings, workdir: Path) -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from auoreo.api.app import create_app

    settings.otel_mode = "local"
    app = create_app(settings, gateway=FakeGateway(), cwd=workdir)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        assert (await c.get("/api/sessions")).status_code == 200
    names = [s.name for s in obs.memory_exporter().get_finished_spans()]
    assert any("/api/sessions" in n for n in names)
