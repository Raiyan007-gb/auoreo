"""G1b: RedactingFormatter is installed on every handler (auoreo + uvicorn loggers)."""

from __future__ import annotations

import io
import json
import logging
import logging.config
from collections.abc import Iterator

import pytest

from auoreo.core.logsetup import configure_logging, install_redaction, uvicorn_log_config
from auoreo.core.redaction import RedactingFormatter
from auoreo.core.settings import Settings

KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


@pytest.fixture
def clean_logging() -> Iterator[None]:
    names = ("", "auoreo", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        n: (logging.getLogger(n).handlers[:], logging.getLogger(n).level, logging.getLogger(n).propagate)
        for n in names
    }
    yield
    for n, (handlers, level, propagate) in saved.items():
        lg = logging.getLogger(n)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def _swap_streams(name: str, buf: io.StringIO) -> None:
    for h in logging.getLogger(name).handlers:
        if isinstance(h, logging.StreamHandler):
            h.setStream(buf)


def test_auoreo_and_uvicorn_loggers_redact(clean_logging: None) -> None:
    st = Settings()
    logging.config.dictConfig(uvicorn_log_config(st))  # what serve.py hands uvicorn
    root = logging.getLogger()
    root.handlers[:] = []
    configure_logging(st, level=logging.INFO)
    buf = io.StringIO()
    for name in ("", "uvicorn", "uvicorn.access"):
        _swap_streams(name, buf)
    logging.getLogger("auoreo.test").warning("calling provider with key %s", KEY)
    logging.getLogger("uvicorn.error").info("Authorization: Bearer %s", KEY)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET", f"/x?api_key={KEY}", "1.1", 200
    )
    out = buf.getvalue()
    print(out)
    assert KEY not in out
    assert "sk-pro…" in out and out.count("\n") >= 3 and "Authorization" in out and "GET /x?" in out
    for name in ("", "uvicorn", "uvicorn.access"):
        assert all(isinstance(h.formatter, RedactingFormatter) for h in logging.getLogger(name).handlers)


def test_existing_handler_formatter_is_wrapped(clean_logging: None) -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("CUSTOM|%(message)s"))
    lg = logging.getLogger("auoreo")
    lg.addHandler(handler)
    install_redaction()
    install_redaction()  # idempotent: never double-wrapped
    lg.error("token=%s", KEY)
    assert buf.getvalue().startswith("CUSTOM|") and KEY not in buf.getvalue()
    assert not isinstance(handler.formatter.inner, RedactingFormatter)  # type: ignore[union-attr]


def test_json_lines_with_extras(clean_logging: None) -> None:
    st = Settings(security={"log_format": "json"})
    logging.getLogger().handlers[:] = []
    configure_logging(st, level=logging.INFO)
    buf = io.StringIO()
    _swap_streams("", buf)
    logging.getLogger("auoreo.run").info(
        "turn done with %s", KEY, extra={"session_id": "ses1", "run_id": "r1", "event_type": "run.completed"}
    )
    row = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert row["session_id"] == "ses1" and row["run_id"] == "r1" and row["event_type"] == "run.completed"
    assert {"ts", "level", "logger", "msg"} <= row.keys() and KEY not in row["msg"]
