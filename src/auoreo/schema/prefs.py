"""Wire models for PREFS.md proposals (G6): agent proposes, human approves/denies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PrefsProposal(BaseModel):
    id: str
    action: Literal["add", "replace", "remove"]
    content: str
    old_text: str | None = None
    reason: str = ""
    session_id: str | None = None
    actor: str = "agent"
    status: Literal["pending", "approved", "denied"]
    created_at: float
    decided_at: float | None = None
    decided_by: str | None = None
    approval_event_id: int | None = Field(
        default=None, description="`seq` of the `prefs.approved` event, also written into the PREFS.md entry"
    )
    deny_reason: str | None = None


class PrefsProposalIn(BaseModel):
    action: Literal["add", "replace", "remove"] = "add"
    content: str = Field(default="", max_length=500)
    old_text: str | None = Field(default=None, max_length=500)
    reason: str = Field(default="", max_length=500)


class PrefsDecision(BaseModel):
    reason: str = Field(default="", max_length=500)
