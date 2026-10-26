"""G5c: opt-in structlog JSON logs (`logging.format = "json"`, spec §20.1).

structlog is configured to *chain through stdlib logging*: `structlog.get_logger()` events
are handed to `logging` (`stdlib.LoggerFactory` + `ProcessorFormatter.wrap_for_formatter`),
so every record — ours, uvicorn's, third-party — flows through the same root handlers and
formatter chain. `JsonFormatter` renders one JSON object per line and then runs the result
through `redaction.redact`, exactly like `redaction.RedactingFormatter`, so secrets stay
masked whichever formatter a handler ends up with.

Fields: `ts` (ISO-8601 UTC), `level`, `logger`, `event`, anything bound with `bound()`
(`session_id`, `run_id`, ...) or passed as stdlib `extra=` (`event_type`), plus `trace_id` /
`span_id` from the current OpenTelemetry span when tracing is on (`observability.current_ids`).
`text` mode (the default) leaves logging untouched.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

import structlog

from auoreo.core import observability as obs
from auoreo.core.redaction import redact

_MARK = "_auoreo_json"
"""Attribute set on the handler `configure()` installs, so re-runs replace rather than stack."""


def _otel_ids(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Link the record to the current span (no-op when OTel is off or no span is current)."""
    for key, value in obs.current_ids().items():
        event.setdefault(key, value)
    return event


SHARED: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    _otel_ids,
]
"""Processors for every record; stdlib records also get their `extra=` fields (`ExtraAdder`)."""


class JsonFormatter(structlog.stdlib.ProcessorFormatter):
    """One redacted JSON object per line for any `LogRecord` (structlog-born or foreign)."""

    def __init__(self) -> None:
        super().__init__(
            foreign_pre_chain=[*SHARED, structlog.stdlib.ExtraAdder()],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(default=str),
            ],
        )

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def formatter() -> logging.Formatter:
    """The JSON formatter (for a logging setup that owns its own handlers)."""
    return JsonFormatter()


def configure(settings: Any) -> bool:
    """Install JSON logging when `settings.logging.format == "json"`; returns whether it did.
    Idempotent: the handler it adds to the root logger is replaced on re-run, and existing
    root handlers get the JSON formatter so nothing prints in two formats."""
    cfg = getattr(settings, "logging", None)
    if getattr(cfg, "format", "text") != "json":
        return False
    structlog.configure(
        processors=[*SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
        else:
            handler.setFormatter(JsonFormatter())
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(getattr(cfg, "level", "INFO"))
    return True


def uvicorn_kwargs(settings: Any) -> dict[str, Any]:
    """In JSON mode uvicorn must not install its own text handlers: `log_config=None` lets
    `uvicorn.*` records propagate to the JSON root handler."""
    fmt = getattr(getattr(settings, "logging", None), "format", "text")
    return {"log_config": None} if fmt == "json" else {}


@contextmanager
def bound(**fields: Any) -> Iterator[None]:
    """Bind `session_id` / `run_id` / ... to every log record emitted inside the block."""
    tokens = structlog.contextvars.bind_contextvars(**{k: v for k, v in fields.items() if v is not None})
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def get_logger(name: str | None = None) -> Any:
    return structlog.stdlib.get_logger(name)
