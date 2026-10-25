"""G4b: tool-catalog knobs (terminal_pty/jobs/monitor, ops_ping, web_fetch/web_search,
document caps, apply_patch diagnostics, delegate_task batches).

Attached to `Settings` as `tools: ToolSettings`; set it in `settings.json`
(`{"tools": {"pty_max_sessions": 4}}`) or as a JSON env var (`AUOREO_TOOLS='{...}'`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MIB = 1024 * 1024


class ToolSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pty_max_sessions: int = Field(default=8, ge=1, le=64)
    """Live `terminal_pty` sessions per AUOREO session."""
    pty_idle_s: float = Field(default=1800.0, ge=30.0)
    """A `terminal_pty` session unused this long is killed by the reaper."""
    monitor_max_s: int = Field(default=600, ge=1, le=3600)
    """Upper bound for one `monitor` call's `timeout_s`."""
    fetch_max_bytes: int = Field(default=5 * MIB, ge=64 * 1024, le=50 * MIB)
    """`web_fetch` body cap (5 MiB; the rest is cut and the result marked truncated)."""
    media_max_bytes: int = Field(default=20 * MIB, ge=MIB, le=200 * MIB)
    """Cap for documents/media read from disk (`doc_read`, `sheet_read`): 20 MiB."""
    tabular_pandas: bool = False
    """`sheet_read` uses pandas by default (extra `tabular`); else only with `engine: pandas`."""
    web_search_timeout_s: float = Field(default=20.0, gt=0, le=60.0)
    """Default per-provider `web_search` timeout; a call may raise it up to 60 s."""
    apply_patch_lsp: Literal["off", "running", "start"] = "running"
    """Diagnostics after `apply_patch`: `running` = only from language servers already
    started in this session; `start` = may start one; `off` = never."""
    apply_patch_lsp_timeout_s: float = Field(default=3.0, gt=0, le=30.0)
    delegate_batch_concurrency: int = Field(default=3, ge=1, le=8)
    """`delegate_task(tasks=[...])`: children run at most this many at a time."""
    ping_tcp_ports: list[int] = Field(default_factory=lambda: [443, 80, 22])
    """`ops_ping` TCP-connect fallback ports (no ping3 / no ICMP permission)."""
