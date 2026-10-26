"""Wire models for the skills hub (G6): sources, hub-managed skills, approve."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
