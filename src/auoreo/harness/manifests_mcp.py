"""`mcp/<name>.yaml` manifests (spec §4, §15, §25.15), split out of `manifests.py`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_RE = r"^[a-z][a-z0-9_]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class McpToolOverride(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    timeout_ms: int | None = Field(default=None, ge=100, le=3_600_000, alias="timeoutMs")


class McpSampling(_Strict):
    enabled: bool = False
    max_tokens: int = Field(default=1024, ge=1, le=32_000)
    """Cap applied to the server's own `maxTokens`."""
    model: str | None = None
    """Model/alias for the aux call; unset = aux routing (`auxiliary.tasks.sampling`)."""


class McpRoots(_Strict):
    """Client `roots` (deprecated in MCP 2026-07-28, kept as a compat shim, default off)."""

    enabled: bool = False
    paths: list[str] = Field(default_factory=list)
    """Explicit allow-list added to the session cwd (absolute, `~` expanded, realpath'd)."""


class McpServerManifest(_Strict):
    """`mcp/<name>.yaml`. Env/header values may be `secret:<name>` references."""

    name: str = Field(pattern=NAME_RE)
    transport: Literal["stdio", "streamable-http", "sse"] = "stdio"
    """`sse` is the legacy HTTP+SSE transport (pre-2025 servers); prefer `streamable-http`."""
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000)
    enabled: bool = True
    exposure: Literal["direct", "deferred"] = "deferred"
    tools: dict[str, McpToolOverride] = Field(default_factory=dict)
    """Per remote-tool overrides keyed by the server's tool name (`timeoutMs` / `timeout_ms`)."""
    sampling: McpSampling = Field(default_factory=lambda: McpSampling())
    """Server→client `sampling/createMessage` (default off; aux call, approval-gated)."""
    roots: McpRoots = Field(default_factory=lambda: McpRoots())
    """`file://` roots = the calling session's cwd + `paths`; `roots/list_changed` on change."""
