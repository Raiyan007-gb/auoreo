"""H1a: shell-floor knobs (spec §19.4, §19.7, §25.10).

Attached to `Settings` as `floors: FloorSettings`; set in `settings.json`
(`{"floors": {"max_inline_command_bytes": 32768}}`). Not settable from a project `.env`
(`core.dotenv` refuses `AUOREO_FLOORS`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FloorSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_inline_command_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    """A shell payload larger than this is saved to `<home>/blocked/blocked-<ts>.sh` (0600)
    instead of running; the tool answers with a `bash <file>` recovery hint (§19.4)."""
