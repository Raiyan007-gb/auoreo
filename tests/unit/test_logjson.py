"""G5c: opt-in structlog JSON logs chained through stdlib logging (spec §20.1)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from auoreo.core import logjson
from auoreo.core import observability as obs
from auoreo.core.settings import Settings
from auoreo.core.settings_obs import LoggingSettings


@pytest.fixture
def root() -> Iterator[logging.Logger]:
    """Snapshot and restore the root logger (handlers, their formatters, level)."""
    lg = logging.getLogger()
    saved = [(h, h.formatter) for h in lg.handlers]
    level = lg.level
    yield lg
    for h in list(lg.handlers):
        if getattr(h, logjson._MARK, False):
            lg.removeHandler(h)
    for h, fmt in saved:
        h.setFormatter(fmt)
    lg.setLevel(level)
    obs.reset()


def _capture(lg: logging.Logger) -> io.StringIO:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logjson.formatter())
    handler.setLevel(logging.DEBUG)
    lg.addHandler(handler)
    return buf


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_settings_default_text_and_env_override(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(home=home).logging == LoggingSettings()
    monkeypatch.setenv("AUOREO_LOGGING", '{"format": "json", "level": "DEBUG"}')
    st = Settings(home=home)
    assert st.logging.format == "json" and st.logging.level == "DEBUG"


def test_text_mode_is_untouched(home: Path, root: logging.Logger) -> None:
    before = [(h, h.formatter) for h in root.handlers]
    assert logjson.configure(Settings(home=home)) is False
    assert [(h, h.formatter) for h in root.handlers] == before
    assert logjson.uvicorn_kwargs(Settings(home=home)) == {}


def test_json_mode_renders_stdlib_and_structlog_records(home: Path, root: logging.Logger) -> None:
    st = Settings(home=home, logging=LoggingSettings(format="json"))
    assert logjson.configure(st) is True
    assert logjson.configure(st) is True  # idempotent: no stacked handlers
    assert sum(bool(getattr(h, logjson._MARK, False)) for h in root.handlers) <= 1
    assert logjson.uvicorn_kwargs(st) == {"log_config": None}
    buf = _capture(root)
    with logjson.bound(session_id="ses_1", run_id="run_1"):
        logging.getLogger("auoreo.test").warning("stdlib %s", "hello", extra={"event_type": "x.y"})
        logjson.get_logger("auoreo.struct").info("structured", tool="bash")
    logging.getLogger("auoreo.test").warning("outside")
    stdlib, structured, outside = _lines(buf)
    assert stdlib["event"] == "stdlib hello" and stdlib["level"] == "warning"
    assert stdlib["logger"] == "auoreo.test" and stdlib["event_type"] == "x.y"
    assert stdlib["session_id"] == "ses_1" and stdlib["run_id"] == "run_1"
    assert str(stdlib["ts"]).endswith("Z") and "trace_id" not in stdlib
    assert structured["event"] == "structured" and structured["tool"] == "bash"
    assert structured["run_id"] == "run_1" and structured["logger"] == "auoreo.struct"
    assert "session_id" not in outside and "_record" not in outside


def test_json_output_is_redacted(home: Path, root: logging.Logger) -> None:
    logjson.configure(Settings(home=home, logging=LoggingSettings(format="json")))
    buf = _capture(root)
    secret = "sk-ant-api03-" + "A" * 40
    logging.getLogger("auoreo.test").warning("key=%s", secret)
    logjson.get_logger("auoreo.struct").warning("call", authorization=f"Bearer {secret}")
    out = buf.getvalue()
    assert secret not in out and len(_lines(buf)) == 2


def test_exceptions_render_inside_the_json_line(home: Path, root: logging.Logger) -> None:
    logjson.configure(Settings(home=home, logging=LoggingSettings(format="json")))
    buf = _capture(root)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("auoreo.test").exception("failed")
    (line,) = _lines(buf)
    assert "ValueError: boom" in str(line["exception"])


def test_trace_and_span_ids_link_to_the_current_otel_span(
    home: Path, root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("opentelemetry.sdk", reason="otel extra not installed")
    monkeypatch.setenv("AUOREO_OTEL_TEST", "1")
    obs.reset()
    assert obs.setup(Settings(home=home, otel_mode="local")) == "local"
    logjson.configure(Settings(home=home, logging=LoggingSettings(format="json")))
    buf = _capture(root)
    with obs.span("agent.run"):
        ids = obs.current_ids()
        logging.getLogger("auoreo.test").warning("inside span")
    assert obs.current_ids() == {}
    (line,) = _lines(buf)
    (finished,) = obs.memory_exporter().get_finished_spans()
    assert ids["trace_id"] == f"{finished.context.trace_id:032x}"
    assert line["trace_id"] == ids["trace_id"] and line["span_id"] == ids["span_id"]


def test_current_ids_empty_when_otel_off(home: Path) -> None:
    obs.reset()
    obs.setup(Settings(home=home))
    with obs.span("agent.run"):
        assert obs.current_ids() == {}
    obs.reset()
