"""Security settings (spec §19): logging format, redaction switches, Landlock layering,
and the unattended-approval policy (§19.9). Attached to `Settings` as `security` / `approvals`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

UnattendedKind = Literal["single_query", "cron", "platform"]
UnattendedAction = Literal["approve", "deny"]


class UnattendedPolicy(BaseModel):
    """What an `ask` does when no human can answer (§19.9). Default: deny everywhere.

    `single_query` = `auoreo run` / MCP / ACP one-shot runs, `cron` = job and webhook
    sessions, `platform` = messaging-connector sessions."""

    single_query: UnattendedAction = "deny"
    cron: UnattendedAction = "deny"
    platform: UnattendedAction = "deny"


class ApprovalSettings(BaseModel):
    unattended: UnattendedPolicy = Field(default_factory=UnattendedPolicy)


class SecuritySettings(BaseModel):
    log_format: Literal["text", "json"] = "text"
    """`json` emits one JSON object per log line (`ts, level, logger, msg` + `session_id,
    run_id, event_type` when passed in `extra`)."""
    redact_terminal_output: bool = True
    """Redact secrets in bash/exec/pwsh output before the model sees it."""
    redact_phones: bool = False
    """Also redact phone numbers (opt-in: false positives on IDs/timestamps)."""
    sandbox_landlock: bool = False
    """Layer Landlock under the `none` backend too (bwrap always layers it when available)."""
