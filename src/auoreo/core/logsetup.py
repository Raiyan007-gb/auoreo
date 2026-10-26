"""Logging setup: every handler formats through `RedactingFormatter` (spec §19.10, L880).

`configure_logging(settings)` runs from the CLI entry point before any command and again on
the server start path; `uvicorn_log_config(settings)` is the dictConfig uvicorn gets so its
own handlers (`uvicorn`, `uvicorn.error`, `uvicorn.access`) redact too. Optional JSON lines
(`security.log_format: json`) carry `session_id/run_id/event_type` when passed in `extra`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from auoreo.core import redaction
from auoreo.core.redaction import RedactingFormatter

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOGGERS = ("", "auoreo", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")
EXTRA_KEYS = ("session_id", "run_id", "event_type")


class JsonLineFormatter(logging.Formatter):
    """One JSON object per record: `ts, level, logger, msg` (+ extras when present)."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in EXTRA_KEYS:
            if (value := getattr(record, key, None)) is not None:
                out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def _log_format(settings: Settings | None) -> str:
    sec = getattr(settings, "security", None)
    obs = getattr(settings, "logging", None)  # G5c `logging.format=json` selects JSON lines too
    if str(getattr(obs, "format", "text")) == "json":
        return "json"
    return str(getattr(sec, "log_format", "text"))


def make_formatter(
    settings: Settings | None = None, inner: logging.Formatter | None = None
) -> RedactingFormatter:
    if inner is None:
        inner = JsonLineFormatter() if _log_format(settings) == "json" else logging.Formatter(TEXT_FORMAT)
    return RedactingFormatter(inner=inner)


def wrap_handler(handler: logging.Handler, settings: Settings | None = None) -> None:
    """Make `handler` redact, keeping its existing formatter (if any) as the inner one."""
    current = handler.formatter
    if isinstance(current, RedactingFormatter):
        return
    handler.setFormatter(make_formatter(settings, inner=current))


def install_redaction(settings: Settings | None = None) -> None:
    """Wrap every handler currently attached to the root/auoreo/uvicorn loggers."""
    for name in LOGGERS:
        for handler in logging.getLogger(name).handlers:
            wrap_handler(handler, settings)


def configure_logging(settings: Settings | None = None, level: int | None = None) -> None:
    """Idempotent. Adds a stderr handler to the root logger when it has none, then wraps
    every known handler's formatter in `RedactingFormatter`."""
    if settings is not None:
        sec = getattr(settings, "security", None)
        redaction.configure(phones=bool(getattr(sec, "redact_phones", False)))
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(make_formatter(settings))
        root.addHandler(handler)
        root.setLevel(level if level is not None else logging.WARNING)
    elif level is not None:
        root.setLevel(level)
    install_redaction(settings)


def uvicorn_log_config(settings: Settings | None = None) -> dict[str, Any]:
    """A uvicorn `log_config` whose formatters are `RedactingFormatter` (JSON when asked)."""
    json_lines = _log_format(settings) == "json"
    factory = "auoreo.core.logsetup.redacting_json" if json_lines else "auoreo.core.logsetup.redacting_text"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"()": factory, "fmt": "%(levelprefix)s %(message)s"},
            "access": {
                "()": factory,
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }


def redacting_text(fmt: str | None = None, **_: Any) -> RedactingFormatter:
    """dictConfig factory: uvicorn's colourised formatter (access-aware) wrapped in redaction."""
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    access = bool(fmt and "request_line" in fmt)
    inner: logging.Formatter = AccessFormatter(fmt) if access else DefaultFormatter(fmt)
    return RedactingFormatter(inner=inner)


def redacting_json(fmt: str | None = None, **_: Any) -> RedactingFormatter:
    return RedactingFormatter(inner=JsonLineFormatter())
