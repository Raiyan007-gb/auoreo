"""Wire models for I4: workspace trust, memory re-embed status."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceTrust(BaseModel):
    workspace: str = Field(description="realpath of the server's working directory")
    trusted: bool
    trust_all: bool = Field(default=False, description="AUOREO_SECURITY_TRUST_ALL_WORKSPACES is set")
    skipped: list[str] = Field(
        default_factory=list, description="`kind/name` definitions skipped from `<cwd>/.auoreo` (untrusted)"
    )


class WorkspaceTrustIn(BaseModel):
    revoke: bool = False


class ReembedStatus(BaseModel):
    embed_model: str
    state: str = Field(description="idle | running | done | failed")
    done: int = 0
    total: int = 0
    stale: int = Field(default=0, description="rows whose stored embed_model differs from the current one")
    error: str | None = None
