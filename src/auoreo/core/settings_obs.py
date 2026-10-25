"""G5c: log output knobs (spec §20.1).

Attached to `Settings` as `logging: LoggingSettings`; set it in `settings.json`
(`{"logging": {"format": "json"}}`) or as a JSON env var (`AUOREO_LOGGING='{"format": "json"}'`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["text", "json"] = "text"
    """`json`: one structlog JSON object per line (`ts, level, logger, event`, bound
    `session_id`/`run_id`, and `trace_id`/`span_id` when an OTel span is current).
    `text`: the stdlib/uvicorn defaults, untouched."""
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    """Root level applied in `json` mode."""
