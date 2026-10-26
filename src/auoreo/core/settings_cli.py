"""G5a: CLI/serve knobs (spec §18). Attached to `Settings` as `cli: CliSettings`; set in
`settings.json` (`{"cli": {"ws_auth": "off"}}`) or by `auoreo serve` flags for one process."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CliSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ws_auth: Literal["token", "off"] = "token"
    """`/ws` handshake auth: `token` = the server password (like HTTP); `off` skips it, which
    `auoreo serve` only allows on a loopback bind."""
    ws_ping_timeout_s: float = Field(default=60.0, gt=0)
    """Protocol-level WebSocket ping timeout handed to uvicorn (`--ws-ping-timeout`)."""
    share_base_url: str | None = None
    """Base for `session share` links (default: the server URL); the token path is appended."""
    attach_timeout_s: float = Field(default=30.0, gt=0)
    """HTTP timeout for `auoreo run --attach` / `session attach` requests (streams excepted)."""
