"""Wire models for the ops endpoints: doctor, audit log, memory facts/files (W0-B)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DoctorCheck(BaseModel):
    check: str = Field(description="Check name, e.g. `python`, `backend:stt`, `credentials`")
    level: Literal["ok", "warn", "error"]
    detail: str
    remedy: str = ""


class DoctorSummary(BaseModel):
    ok: int = 0
    warn: int = 0
    error: int = 0


class DoctorReport(BaseModel):
    """Read-only environment report: never repairs, never includes secret values."""

    checks: list[DoctorCheck]
    summary: DoctorSummary


class AuditEntry(BaseModel):
    seq: int
    ts: datetime
    session_id: str | None = None
    actor: str
    action: str
    decision: str = ""
    reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict, description="Redacted detail payload")
    prev_hash: str
    row_hash: str


class AuditPage(BaseModel):
    rows: list[AuditEntry]
    next_after_seq: int | None = Field(
        default=None, description="Pass as `after_seq` for the next page; null at the end"
    )


class AuditVerify(BaseModel):
    ok: bool
    first_break_seq: int | None = None
    rows: int = Field(description="Total audit rows checked")


class MemoryFact(BaseModel):
    id: int
    content: str
    category: str
    trust: float
    source: str | None = None
    pinned: bool = False
    archived: bool = False
    created_at: float
    updated_at: float
    archived_at: float | None = None
    last_used_at: float | None = None
    use_count: int = 0
    valid_from: float | None = None
    valid_to: float | None = None
    supersedes_id: int | None = None


class MemoryFactPage(BaseModel):
    facts: list[MemoryFact]
    total: int = Field(description="Matches before limit/offset")


class MemoryFile(BaseModel):
    name: str
    exists: bool
    content: str = Field(default="", description="Markdown with secrets redacted")
    size: int = 0


class MemoryFiles(BaseModel):
    dir: str
    files: list[MemoryFile]


WelcomeStep = Literal["doctor", "credentials", "model", "tunnel", "theme", "demo", "wizard"]


class WelcomeEvent(BaseModel):
    """One onboarding step outcome; recorded in the audit log as `welcome.<action>`."""

    model_config = ConfigDict(extra="forbid")
    step: WelcomeStep
    action: Literal["completed", "skipped", "dismissed", "finished"]
