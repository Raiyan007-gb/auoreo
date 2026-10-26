"""Wire models for the skills hub (G6): sources, hub-managed skills, approve."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HubInstalledSkill(BaseModel):
    name: str
    status: Literal["quarantined", "approved"]
    integrity: Literal["ok", "modified", "missing"]
    sha256: str
    tap: str
    version: str = ""
    installed_at: float
    approved_at: float | None = None
    files: list[str] = Field(default_factory=list)
    has_scripts: bool = False
    lint: list[str] = Field(default_factory=list)


class McpSkillSource(BaseModel):
    source: str = Field(description="`mcp:<server>`")
    server: str
    name: str
    uri: str
    description: str = ""


class SkillSources(BaseModel):
    taps: list[str]
    mcp: list[McpSkillSource]
    installed: list[HubInstalledSkill]


# --- I5: hub search + install from the API/UI
class HubSearchHit(BaseModel):
    name: str
    description: str
    tap: str
    version: str = ""
    installed: Literal["quarantined", "approved"] | None = None
    """Lock status when this skill is already hub-managed."""


class HubSearchResult(BaseModel):
    query: str
    taps: list[str]
    hits: list[HubSearchHit]
    errors: list[str] = Field(default_factory=list)
    """Per-tap failures (unreachable index, bad clone URL); the other taps still answer."""


class HubInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    tap: str | None = Field(default=None, description="Only look in this configured tap.")
    force: bool = Field(default=False, description="Reinstall (goes back to quarantine).")


class HubRemoved(BaseModel):
    name: str
    removed: bool
    status: Literal["quarantined", "approved"]
